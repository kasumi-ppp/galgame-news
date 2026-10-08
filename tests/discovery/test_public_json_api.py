import asyncio
import json
from types import SimpleNamespace

import pytest

from galgame_news.discovery.async_http import AsyncHttpClient
from galgame_news.discovery.http import SafeHttpClient, UnsafeUrlError, ResponseTooLarge


def test_json_post_isolated_and_uses_public_network_limits():
    calls = []
    def transport(url, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(url=url, status_code=200, headers={"content-type": "application/json"}, content=b'{"results": []}')
    async def run():
        async with AsyncHttpClient(transport=transport, resolver=lambda _: ["1.1.1.1"], trust_env=True) as client:
            response = await client.post_json("https://api.vndb.org/kana/vn", {"filters": ["id", "=", "v1"]})
            assert response.json() == {"results": []}
            assert client.trust_env is True
            with pytest.raises(UnsafeUrlError):
                await client.post_json("https://other.example/kana/vn", {})
    asyncio.run(run())
    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert "authorization" not in calls[0]["headers"]
    assert calls[0]["json"]["filters"][2] == "v1"


@pytest.mark.parametrize("asynchronous", [True, False])
def test_public_json_post_refuses_redirect_and_oversize(asynchronous):
    def transport(url, **kwargs):
        return SimpleNamespace(url=url, status_code=302, headers={"location": "https://other.example"}, content=b'{}')
    if asynchronous:
        async def run():
            async with AsyncHttpClient(transport=transport, resolver=lambda _: ["1.1.1.1"]) as client:
                with pytest.raises(UnsafeUrlError):
                    await client.post_json("https://api.vndb.org/kana/vn", {})
        asyncio.run(run())
    else:
        with pytest.raises(UnsafeUrlError):
            SafeHttpClient(transport=transport, resolver=lambda _: ["1.1.1.1"]).post_json("https://api.vndb.org/kana/vn", {})
    def large(url, **kwargs):
        return SimpleNamespace(url=url, status_code=200, headers={}, content=b'x' * 100)
    if asynchronous:
        async def run_large():
            async with AsyncHttpClient(transport=large, resolver=lambda _: ["1.1.1.1"], max_response_bytes=10) as client:
                with pytest.raises(ResponseTooLarge):
                    await client.post_json("https://api.vndb.org/kana/vn", {})
        asyncio.run(run_large())
    else:
        with pytest.raises(ResponseTooLarge):
            SafeHttpClient(transport=large, resolver=lambda _: ["1.1.1.1"], max_response_bytes=10).post_json("https://api.vndb.org/kana/vn", {})
