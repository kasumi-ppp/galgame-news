"""TwitterAPI.io transport used to recover image media from X posts."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx

from ..settings.credentials import CredentialStore


_API_BASE = "https://api.twitterapi.io"
_STATUS_ID = re.compile(r"/(?:i/web/)?status/(\d+)(?:/|$)", re.IGNORECASE)


def _configured_tokens() -> tuple[str | None, str | None]:
    """Resolve TwitterAPI.io and X API tokens without reading plain settings."""

    twitterapi_key = next((
        os.environ.get(name, "").strip()
        for name in ("TWITTERAPI_KEY", "TWITTERAPI_IO_KEY")
        if os.environ.get(name, "").strip()
    ), None)
    x_bearer_token = os.environ.get("X_BEARER_TOKEN", "").strip() or None
    if twitterapi_key and x_bearer_token:
        return twitterapi_key, x_bearer_token
    try:
        store = CredentialStore()
        return (
            twitterapi_key or store.get_twitterapi_api_key(),
            x_bearer_token or store.get_x_bearer_token(),
        )
    except Exception:
        # Headless CLI installs may not include the optional keyring package.
        return twitterapi_key, x_bearer_token


class TwitterApiIoMediaTransport:
    """Look up a single public X status and return its structured response."""

    def __call__(self, status_url: str, *, token: str | None, timeout: float):
        parts = urlsplit(status_url)
        host = (parts.hostname or "").casefold()
        if host not in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
            raise ValueError("X media lookup requires an X status URL")
        match = _STATUS_ID.search(parts.path)
        if not match:
            raise ValueError("X status URL does not contain a numeric post ID")
        if not token:
            raise ValueError("TwitterAPI.io API key is not configured")

        response = httpx.get(
            f"{_API_BASE}/twitter/tweets",
            params={"tweet_ids": match.group(1)},
            headers={"X-API-Key": token, "User-Agent": "WeeklyImageToolbox/1.0"},
            timeout=min(max(float(timeout), 1.0), 8.0),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("TwitterAPI.io returned an unexpected response")
        return payload


class XApiV2MediaTransport:
    """Fetch one X post with its photo media expansion using a bearer token."""

    def __call__(self, status_url: str, *, token: str | None, timeout: float):
        parts = urlsplit(status_url)
        host = (parts.hostname or "").casefold()
        if host not in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
            raise ValueError("X media lookup requires an X status URL")
        match = _STATUS_ID.search(parts.path)
        if not match:
            raise ValueError("X status URL does not contain a numeric post ID")
        if not token:
            raise ValueError("X bearer token is not configured")

        response = httpx.get(
            f"https://api.x.com/2/tweets/{match.group(1)}",
            params={
                "expansions": "attachments.media_keys",
                "tweet.fields": "attachments,entities,text",
                "media.fields": "alt_text,media_key,type,url,preview_image_url,width,height",
            },
            headers={"Authorization": f"Bearer {token}", "User-Agent": "WeeklyImageToolbox/1.0"},
            timeout=min(max(float(timeout), 1.0), 8.0),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("X API returned an unexpected response")
        return payload


class SocialDataTweetTransport:
    """Fetch and task-cache one SocialData tweet lookup by numeric status ID."""

    def __init__(self, *, http_get: Callable[..., object] | None = None, response_cache: dict[str, object] | None = None):
        self.http_get = http_get or httpx.get
        self.response_cache = response_cache if response_cache is not None else {}

    def __call__(self, status_url: str, *, token: str | None, timeout: float):
        parts = urlsplit(status_url)
        host = (parts.hostname or "").casefold()
        if host not in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
            raise ValueError("SocialData lookup requires an X status URL")
        match = _STATUS_ID.search(parts.path)
        if not match:
            raise ValueError("X status URL does not contain a numeric post ID")
        if not token:
            raise ValueError("SocialData API key is not configured")
        status_id = match.group(1)
        if status_id in self.response_cache:
            cached = self.response_cache[status_id]
            if cached is None:
                raise RuntimeError("SocialData lookup for this post already failed")
            return cached
        try:
            response = self.http_get(
                f"https://api.socialdata.tools/twitter/tweets/{status_id}",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "WeeklyImageToolbox/1.0"},
                timeout=min(max(float(timeout), 1.0), 8.0),
            )
            status = int(getattr(response, "status_code", 200))
            if status < 200 or status >= 300:
                raise RuntimeError(f"SocialData returned HTTP {status}")
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("SocialData returned an unexpected response")
        except Exception as exc:
            self.response_cache[status_id] = None
            if isinstance(exc, RuntimeError) and str(exc).startswith("SocialData "):
                raise
            raise RuntimeError(f"SocialData request failed ({type(exc).__name__})") from None
        self.response_cache[status_id] = payload
        return payload


def create_x_adapter(*, public_transport=None, public_resolver=None, use_socialdata: bool = False, response_cache=None, socialdata_transport=None):
    """Build the X adapter with the configured structured-media backend."""

    from .adapters.x import XAdapter

    if use_socialdata:
        try:
            token = CredentialStore().get_socialdata_api_key() or os.environ.get("SOCIALDATA_API_KEY", "").strip() or None
        except Exception:
            token = os.environ.get("SOCIALDATA_API_KEY", "").strip() or None
        transport = socialdata_transport or SocialDataTweetTransport(response_cache=response_cache)
        return XAdapter(
            token=token,
            transport=transport,
            socialdata_mode=True,
            public_transport=public_transport,
            public_resolver=public_resolver,
            public_timeout=4.0,
        )

    twitterapi_key, x_bearer_token = _configured_tokens()
    token = twitterapi_key or x_bearer_token
    transport = (
        TwitterApiIoMediaTransport() if twitterapi_key
        else XApiV2MediaTransport() if x_bearer_token
        else None
    )
    return XAdapter(
        token=token,
        transport=transport,
        public_transport=public_transport,
        public_resolver=public_resolver,
        public_timeout=4.0,
    )
