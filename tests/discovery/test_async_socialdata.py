from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from galgame_news.discovery.async_x_api import AsyncSocialDataTweetTransport
from galgame_news.discovery.adapters.x import XAdapter
from galgame_news.pipeline import CancellationToken


def test_concurrent_photo_and_video_consumers_use_one_post_query():
    async def run():
        calls = []
        payload = {"id_str": "123", "extended_entities": {"media": [
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/photo.jpg"},
            {"type": "video", "video_info": {"variants": [{"bitrate": 1000, "content_type": "video/mp4", "url": "https://video.twimg.com/a.mp4"}]}},
        ]}}
        async def handler(request):
            calls.append(request)
            await asyncio.sleep(0.02)
            return httpx.Response(200, json=payload)
        api = AsyncSocialDataTweetTransport(transport=httpx.MockTransport(handler))
        try:
            results = await asyncio.gather(*(
                api.lookup("https://x.com/studio/status/123", token="secret-test-only", timeout=1)
                for _ in range(12)
            ))
            assert len(calls) == api.request_count == 1
            assert all(result == payload for result in results)
            assert XAdapter._api_image_urls(results[0])
            # Video and photo clients share the exact response object.
            assert results[0] is results[-1]
            assert "secret-test-only" not in json.dumps(results)
            assert calls[0].url.host == "api.socialdata.tools"
        finally:
            await api.close()
    asyncio.run(run())


def test_failed_post_is_cached_without_retry_or_secret_in_error():
    async def run():
        calls = []
        async def handler(request):
            calls.append(request)
            raise httpx.ConnectError("credential secret-test-only", request=request)
        api = AsyncSocialDataTweetTransport(transport=httpx.MockTransport(handler))
        try:
            for _ in range(3):
                with pytest.raises(RuntimeError, match="SocialData request failed") as error:
                    await api.lookup("https://twitter.com/studio/status/123", token="secret-test-only", timeout=1)
                assert "secret-test-only" not in str(error.value)
            assert len(calls) == 1
        finally:
            await api.close()
    asyncio.run(run())


def test_socialdata_cancellation_aborts_header_wait_without_extra_query():
    async def run():
        token = CancellationToken()
        entered = asyncio.Event()
        async def handler(request):
            entered.set()
            await asyncio.sleep(10)
            return httpx.Response(200, json={})
        api = AsyncSocialDataTweetTransport(transport=httpx.MockTransport(handler), cancellation=token)
        task = asyncio.create_task(api.lookup("https://x.com/a/status/123", token="secret", timeout=8))
        await entered.wait()
        token.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert api.request_count == 1
        await api.close()
    asyncio.run(run())
