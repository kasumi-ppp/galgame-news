"""Task-local, single-request SocialData lookup, isolated from public HTTP."""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit

import httpx

from .x_api import _STATUS_ID


class AsyncSocialDataTweetTransport:
    def __init__(self, *, concurrency=2, transport=None, cancellation=None):
        self._client = httpx.AsyncClient(transport=transport, follow_redirects=False)
        self._limit = asyncio.Semaphore(concurrency)
        self._tasks = {}
        self._token = cancellation
        self.request_count = 0

    async def lookup(self, status_url, *, token, timeout):
        parts = urlsplit(status_url)
        match = _STATUS_ID.search(parts.path)
        if (parts.hostname or "").casefold() not in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"} or not match:
            raise ValueError("SocialData lookup requires an X status URL")
        if not token:
            raise ValueError("SocialData API key is not configured")
        post_id = match.group(1)
        if post_id not in self._tasks:
            self._tasks[post_id] = asyncio.create_task(self._query(post_id, token, timeout))
        task = self._tasks[post_id]
        while not task.done():
            if self._token is not None and self._token.is_cancelled:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise asyncio.CancelledError()
            await asyncio.wait({task}, timeout=0.05)
        return await asyncio.shield(task)

    async def _query(self, post_id, token, timeout):
        async with self._limit:
            while self._token is not None and self._token.is_paused and not self._token.is_cancelled:
                await asyncio.sleep(0.05)
            if self._token is not None and self._token.is_cancelled:
                raise asyncio.CancelledError()
            self.request_count += 1
            try:
                async with self._client.stream(
                    "GET", f"https://api.socialdata.tools/twitter/tweets/{post_id}",
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    timeout=min(max(float(timeout), 1.0), 8.0),
                ) as response:
                    if not 200 <= response.status_code < 300:
                        raise RuntimeError(f"SocialData returned HTTP {response.status_code}")
                    data = bytearray()
                    async for block in response.aiter_bytes():
                        if len(data) + len(block) > 5_242_880:
                            raise RuntimeError("SocialData response exceeds size limit")
                        data.extend(block)
                payload = json.loads(data)
                if not isinstance(payload, dict):
                    raise RuntimeError("SocialData returned an unexpected response")
                return payload
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Never include transport exception messages: they can contain
                # request headers, credentials or complete response bodies.
                if isinstance(exc, RuntimeError) and str(exc).startswith("SocialData "):
                    raise
                raise RuntimeError(f"SocialData request failed ({type(exc).__name__})") from None

    async def close(self):
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        await self._client.aclose()
