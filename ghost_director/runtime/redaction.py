"""Redact provider credentials before any observable persistence boundary."""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


class Redactor:
    def __init__(self, secrets: tuple[str, ...] = ()):
        self.secrets = tuple(sorted((s for s in secrets if s), key=len, reverse=True))

    def text(self, value: str) -> str:
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
        value = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{16,}|fal_[A-Za-z0-9_-]{16,})\b", "[REDACTED]", value)
        # Signed transport URLs are not durable artifact identifiers. Drop all
        # query/fragment fields rather than guessing every provider's token name.
        def clean_url(match):
            raw = match.group(0)
            parsed = urlsplit(raw)
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        return re.sub(r"https?://[^\s\"<>]+", clean_url, value)

    def value(self, value):
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                normalized = str(key).lower().replace("-", "_")
                if normalized in {"authorization", "api_key", "apikey", "access_token", "refresh_token", "secret", "password", "cookie", "set_cookie"}:
                    result[str(key)] = "[REDACTED]"
                else:
                    result[str(key)] = self.value(child)
            return result
        if isinstance(value, (list, tuple)):
            return [self.value(child) for child in value]
        if isinstance(value, str):
            return self.text(value)
        return value
