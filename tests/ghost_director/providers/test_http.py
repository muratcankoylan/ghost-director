import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import httpx
from ghost_director.config import Settings
from ghost_director.providers.http import ProviderHTTP, ProviderError
from ghost_director.providers.runner import ProviderRunner
from ghost_director.jobs.store import ProductionStore

class HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_documented_fal_storage_bucket_never_gets_credentials(self):
        seen=[]
        def respond(request):
            seen.append(request)
            if request.url.host=='openrouter.ai':
                self.assertEqual(request.headers.get('authorization'),'Bearer video-key')
                return httpx.Response(302,headers={'location':'https://storage.googleapis.com/falserverless/output.mp4?signature=opaque'})
            self.assertNotIn('authorization',request.headers)
            return httpx.Response(200,content=b'video')
        client=ProviderHTTP(Settings('meta-key','video-key','fal-key',True),httpx.MockTransport(respond))
        try:
            self.assertEqual(await client.download('https://storage.googleapis.com/falserverless/output.mp4'),b'video')
            self.assertEqual(await client.download('https://openrouter.ai/api/v1/videos/job/content'),b'video')
            self.assertEqual(len(seen),3)
        finally:await client.close()

    async def test_storage_scope_rejects_other_buckets_hosts_and_path_escape(self):
        seen=[]
        def respond(request):
            seen.append(request)
            return httpx.Response(200,content=b'not allowed')
        client=ProviderHTTP(Settings('meta-key','video-key','fal-key',True),httpx.MockTransport(respond))
        try:
            for url in (
                'https://storage.googleapis.com/another-bucket/output.mp4',
                'https://storage.googleapis.com/falserverless-lookalike/output.mp4',
                'https://storage.googleapis.com.attacker.invalid/falserverless/output.mp4',
                'https://sub.storage.googleapis.com/falserverless/output.mp4',
                'https://storage.googleapis.com/falserverless/../another-bucket/output.mp4',
                'https://storage.googleapis.com/falserverless/%2e%2e/another-bucket/output.mp4',
            ):
                with self.subTest(url=url),self.assertRaises(ValueError):
                    await client.download(url)
            self.assertEqual(seen,[])
        finally:await client.close()

    async def test_error_message_preserves_diagnostic_without_keys_or_signed_urls(self):
        secret='private-test-key-not-for-output'
        body={'error':{'message':f'Invalid reference: {secret} https://cdn.fal.media/file?signature=private data:image/png;base64,AAAA'},'request':{'authorization':secret}}
        client=ProviderHTTP(Settings(secret,'video-key','fal-key',True),httpx.MockTransport(lambda request:httpx.Response(400,json=body)))
        try:
            with self.assertRaises(ProviderError) as caught:
                await client.request('meta','POST','https://api.meta.ai/v1/images/generations',{})
            message=str(caught.exception)
            self.assertIn('Invalid reference',message)
            self.assertNotIn(secret,message)
            self.assertNotIn('signature',message)
            self.assertNotIn('AAAA',message)
            self.assertFalse(caught.exception.uncertain)
        finally: await client.close()

    async def test_content_origin_authentication_is_removed_on_cdn_redirect(self):
        seen = []
        def respond(request):
            seen.append(request)
            if len(seen) == 1:
                self.assertEqual(request.headers.get('authorization'), 'Bearer test-video')
                return httpx.Response(302, headers={'location': 'https://cdn.bytepluscdn.com/take.mp4?signature=opaque'})
            self.assertNotIn('authorization', request.headers)
            return httpx.Response(200, content=b'video-bytes')
        client = ProviderHTTP(Settings('test-meta', 'test-video', 'test-fal', True), httpx.MockTransport(respond))
        try:
            result = await client.download('https://openrouter.ai/api/v1/videos/job/content')
            self.assertEqual(result, b'video-bytes')
            self.assertEqual(len(seen), 2)
            self.assertEqual(seen[1].url.params['signature'], 'opaque')
        finally:
            await client.close()

    async def test_download_rejects_lookalike_origin_before_sending_credentials(self):
        seen = []
        def respond(request):
            seen.append(request)
            return httpx.Response(302, headers={'location': 'https://openrouter.ai.attacker.invalid/api/v1/videos/job/content'})
        client = ProviderHTTP(Settings('test-meta', 'test-video', 'test-fal', True), httpx.MockTransport(respond))
        try:
            with self.assertRaises(ValueError):
                await client.download('https://openrouter.ai.attacker.invalid/api/v1/videos/job/content')
            self.assertEqual(seen, [])
            with self.assertRaises(ValueError):
                await client.download('https://openrouter.ai/api/v1/videos/job/content')
            self.assertEqual(len(seen), 1)
        finally:
            await client.close()

    async def test_download_subdomain_and_other_path_do_not_receive_origin_key(self):
        seen = []
        def respond(request):
            seen.append(request)
            self.assertNotIn('authorization', request.headers)
            return httpx.Response(200, content=b'asset')
        client = ProviderHTTP(Settings('test-meta', 'test-video', 'test-fal', True), httpx.MockTransport(respond))
        try:
            await client.download('https://cdn.openrouter.ai/api/v1/videos/job/content')
            await client.download('https://openrouter.ai/public/asset.mp4')
            self.assertEqual(len(seen), 2)
        finally:
            await client.close()

    async def test_credentials_never_follow_redirect(self):
        seen=[]
        def respond(request):
            seen.append(request)
            return httpx.Response(302,headers={'location':'https://attacker.invalid'})
        client=ProviderHTTP(Settings('secret','or','fal',True),httpx.MockTransport(respond))
        with self.assertRaises(ProviderError): await client.request('meta','POST','https://api.meta.ai/v1/chat/completions',{})
        self.assertEqual(len(seen),1)
        with self.assertRaises(ValueError): await client.request('meta','GET','https://api.meta.ai.attacker.invalid')
        await client.close()

    async def test_uncertain_paid_post_is_not_retried(self):
        calls=[]
        def respond(request):
            calls.append(request)
            raise httpx.ReadTimeout('uncertain')
        with TemporaryDirectory() as root:
            settings=Settings('secret','or','fal',True,Path(root))
            client=ProviderHTTP(settings,httpx.MockTransport(respond))
            store=ProductionStore(root,opening_entries=())
            runner=ProviderRunner(store,settings,client)
            with self.assertRaises(ProviderError): await runner.image('original reference')
            self.assertEqual(len(calls),1)
            self.assertEqual(store.snapshot()['jobs'][0]['status'],'submission_unknown')
            self.assertEqual(store.budget()['categories']['meta']['reserved_usd'],.02)
            await client.close()

    async def test_auth_failure_releases_reservation(self):
        with TemporaryDirectory() as root:
            settings=Settings('secret','or','fal',True,Path(root))
            client=ProviderHTTP(settings,httpx.MockTransport(lambda request:httpx.Response(401)))
            store=ProductionStore(root,opening_entries=())
            with self.assertRaises(ProviderError): await ProviderRunner(store,settings,client).image('reference')
            self.assertEqual(store.budget()['committed_usd'],0)
            self.assertEqual(store.snapshot()['jobs'][0]['status'],'failed')
            await client.close()
