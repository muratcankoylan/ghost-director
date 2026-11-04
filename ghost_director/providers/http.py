from __future__ import annotations
import base64
import json
import re
from pathlib import Path
from urllib.parse import urlsplit, urljoin, unquote
import httpx
from ghost_director.config import Settings

class ProviderError(RuntimeError):
    def __init__(self, message, *, uncertain=False, status=None):
        super().__init__(message)
        self.uncertain = uncertain
        self.status = status


def safe_error_detail(response, secrets):
    """Keep the documented error message, never the raw request/response body."""
    if len(response.content) > 50_000:
        return ''
    try:
        body = response.json()
        error = body.get('error', {}) if isinstance(body, dict) else {}
        message = error.get('message', '') if isinstance(error, dict) else ''
        if not isinstance(message, str): message = ''
        if not message and isinstance(body, dict) and isinstance(body.get('detail'), list):
            # fal validation errors expose detail[].msg. Never include their
            # input, context or location fields, which can echo the upload.
            message = '; '.join(row['msg'] for row in body['detail'][:8]
                                if isinstance(row, dict) and isinstance(row.get('msg'), str))
    except ValueError:
        return ''
    for secret in secrets:
        if secret:
            from urllib.parse import quote
            for variant in (secret, quote(secret, safe=''), json.dumps(secret)[1:-1], base64.b64encode(secret.encode()).decode()):
                message = message.replace(variant, '[credential redacted]')
    message = re.sub(r'data:[^\s"\']+', '[data redacted]', message)
    message = re.sub(r'https?://[^\s"\']+', '[url redacted]', message)
    message = re.sub(r'[A-Za-z0-9_+/=\-]{40,}', '[opaque value redacted]', message)
    return ' '.join(message.split())[:600]


def checked_url(url: str, hosts: tuple[str,...], *, subdomains=False):
    parsed = urlsplit(url)
    allowed = parsed.hostname in hosts or (subdomains and any((parsed.hostname or '').endswith('.'+host) for host in hosts))
    if parsed.scheme != 'https' or not allowed or parsed.username or parsed.password or parsed.port not in (None,443):
        raise ValueError('Provider destination rejected')
    return url


def data_uri(path: Path, mime='image/png'):
    raw = path.read_bytes()
    if len(raw) > 20_000_000:
        raise ValueError('Reference exceeds 20 MB upload limit')
    return f'data:{mime};base64,' + base64.b64encode(raw).decode()


class ProviderHTTP:
    def __init__(self, settings: Settings, transport=None):
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(240, connect=20), follow_redirects=False, transport=transport)

    async def close(self):
        await self.client.aclose()

    async def request(self, provider, method, url, payload=None, *, sse=False):
        key, host, scheme = {
            'meta':(self.settings.meta_key,'api.meta.ai','Bearer'),
            'openrouter':(self.settings.openrouter_key,'openrouter.ai','Bearer'),
            'fal':(self.settings.fal_key,'queue.fal.run','Key'),
        }[provider]
        checked_url(url,(host,))
        if not key or not self.settings.allow_live:
            raise ValueError('Live provider credentials or authorization are absent')
        headers = {'Authorization':scheme+' '+key,'Content-Type':'application/json'}
        if sse: headers['Accept']='text/event-stream'
        try:
            response = await self.client.request(method,url,headers=headers,json=payload)
        except httpx.TransportError as error:
            raise ProviderError(f'{provider} transport {type(error).__name__}',uncertain=method=='POST') from None
        if response.status_code >= 300:
            detail=safe_error_detail(response,(self.settings.meta_key,self.settings.openrouter_key,self.settings.fal_key))
            suffix=': '+detail if detail else ''
            raise ProviderError(f'{provider} returned HTTP {response.status_code}{suffix}',uncertain=method=='POST' and (response.status_code>=500 or response.status_code==408),status=response.status_code)
        if len(response.content)>50_000_000:
            raise ProviderError('Provider response exceeds 50 MB', uncertain=method=='POST')
        try:
            if sse:
                import json
                return [json.loads(line[5:].strip()) for line in response.text.splitlines() if line.startswith('data:') and line[5:].strip()!='[DONE]']
            return response.json()
        except ValueError:
            raise ProviderError('Provider response was not valid JSON',uncertain=method=='POST') from None

    async def download(self, url, *, limit=120_000_000):
        # Provider content endpoints authenticate only to their exact origin; signed
        # CDN artifact URLs receive no Authorization header. Restrict to the
        # documented provider delivery domains, including each redirect target.
        hosts=('fal.media','fal.ai','openrouter.ai','byteoversea.com','bytepluscdn.com','volces.com','together.ai','replicate.delivery')
        for _ in range(5):
            parsed=urlsplit(url)
            if parsed.hostname=='storage.googleapis.com' and parsed.path.startswith('/falserverless/'):
                checked_url(url,('storage.googleapis.com',))
                decoded_path=unquote(parsed.path)
                if '\\' in decoded_path or any(part in ('.','..') for part in decoded_path.split('/')):
                    raise ValueError('Provider content path is outside the documented bucket scope')
            else:checked_url(url,hosts,subdomains=True)
            headers={}
            if parsed.hostname=='openrouter.ai' and parsed.path.startswith('/api/v1/videos/'):
                if not self.settings.openrouter_key or not self.settings.allow_live:raise ValueError('Live provider credentials or authorization are absent')
                headers={'Authorization':'Bearer '+self.settings.openrouter_key}
            async with self.client.stream('GET',url,headers=headers) as response:
                if response.status_code in (301,302,303,307,308):
                    url=urljoin(url,response.headers['location']); continue
                if response.status_code != 200:
                    raise ProviderError(f'Artifact download HTTP {response.status_code}')
                chunks=[]; size=0
                async for chunk in response.aiter_bytes():
                    size+=len(chunk)
                    if size>limit: raise ProviderError('Artifact exceeds download limit')
                    chunks.append(chunk)
                return b''.join(chunks)
        raise ProviderError('Too many artifact redirects')
