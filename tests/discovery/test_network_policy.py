"""Mocked coverage for task network settings and bounded fallback requests."""

import asyncio
from contextlib import contextmanager
import gc
from types import SimpleNamespace

import httpx
import pytest

from galgame_news.config import CONCURRENCY_FIELDS, load_config
from galgame_news.discovery.async_http import AsyncHttpClient
from galgame_news.discovery.http import ResponseTooLarge, SafeHttpClient, UnsafeUrlError
from galgame_news.pipeline.checkpoint import config_hash
from galgame_news.pipeline.contracts import CancellationToken
from galgame_news.pipeline.network_runtime import TaskNetworkRuntime


PUBLIC = lambda _host: ["93.184.216.34"]


def mock_native_stream(monkeypatch, handler):
    options = []

    @contextmanager
    def stream(method, url, **kwargs):
        options.append(dict(kwargs))
        trust_env = kwargs.pop("trust_env")
        with httpx.Client(transport=httpx.MockTransport(handler), trust_env=trust_env) as client:
            with client.stream(method, url, **kwargs) as response:
                yield response

    monkeypatch.setattr(httpx, "stream", stream)
    return options


def test_proxy_setting_defaults_and_does_not_change_checkpoint_hash(tmp_path):
    default = load_config()
    assert default.network.trust_env is True
    override = tmp_path / "network.toml"
    override.write_text("[network]\ntrust_env=false\n", encoding="utf-8")
    configured = load_config(override)
    assert configured.network.trust_env is False
    assert "trust_env" in CONCURRENCY_FIELDS
    assert config_hash(default) == config_hash(configured)


@pytest.mark.parametrize("trust_env", [True, False])
def test_sync_client_forwards_environment_policy(monkeypatch, trust_env):
    options = mock_native_stream(monkeypatch, lambda request: httpx.Response(200, content=b"ok"))
    client = SafeHttpClient(resolver=PUBLIC, trust_env=trust_env)
    assert client.get("https://public.example/image").content == b"ok"
    assert [entry["trust_env"] for entry in options] == [trust_env]
    assert [entry["follow_redirects"] for entry in options] == [False]


@pytest.mark.parametrize("trust_env", [True, False])
def test_async_client_forwards_environment_policy(monkeypatch, trust_env):
    original = httpx.AsyncClient
    seen = []

    def make_client(**kwargs):
        seen.append(kwargs["trust_env"])
        return original(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"ok")), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", make_client)

    async def scenario():
        async with AsyncHttpClient(resolver=PUBLIC, trust_env=trust_env) as client:
            assert (await client.get("https://public.example/image")).content == b"ok"
        assert seen == [trust_env]

    asyncio.run(scenario())


def test_task_runtime_forwards_environment_policy_to_page_and_image_clients():
    async def scenario():
        config = load_config()
        config.network.trust_env = False
        runner = SimpleNamespace(config=config, source_transport=None, image_transport=None)
        runtime = TaskNetworkRuntime(runner, CancellationToken())
        try:
            assert runtime.page_client.trust_env is False
            assert runtime.image_client.trust_env is False
            assert runtime.socialdata._client is not runtime.page_client._client
        finally:
            await runtime.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["cancel", "timeout"])
def test_wait_drains_cancel_cleanup_exception(mode):
    async def scenario():
        loop = asyncio.get_running_loop()
        unhandled = []
        loop.set_exception_handler(lambda _loop, context: unhandled.append(context))
        started = asyncio.Event()
        cleaned = asyncio.Event()

        async def operation():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0)
                cleaned.set()
                raise RuntimeError("error during cancellation cleanup")

        async with AsyncHttpClient(resolver=PUBLIC) as client:
            waiter = asyncio.create_task(client._wait(operation(), timeout=0.01 if mode == "timeout" else None))
            await started.wait()
            if mode == "cancel":
                waiter.cancel()
            error = asyncio.CancelledError if mode == "cancel" else httpx.TimeoutException
            with pytest.raises(error):
                await waiter
            assert cleaned.is_set()
            del waiter
            gc.collect()
            await asyncio.sleep(0)
            assert unhandled == []

    asyncio.run(scenario())


@pytest.mark.parametrize("error_kind", ["status", "tls"])
def test_sync_per_call_attempt_limit_preserves_client_default(error_kind):
    calls = []

    def transport(url, **_kwargs):
        calls.append(url)
        if error_kind == "tls":
            raise httpx.ConnectError("hostname mismatch")
        return httpx.Response(503, request=httpx.Request("GET", url))

    client = SafeHttpClient(transport=transport, resolver=PUBLIC, max_retries=3)
    if error_kind == "tls":
        with pytest.raises(httpx.ConnectError):
            client.get("https://public.example/image", max_retries=1)
    else:
        assert client.get("https://public.example/image", max_retries=1).status_code == 503
    assert calls == ["https://public.example/image"]
    assert client.max_retries == 3


@pytest.mark.parametrize("error_kind", ["status", "tls"])
def test_async_per_call_attempt_limit_preserves_client_default(tmp_path, error_kind):
    async def scenario():
        calls = []

        async def transport(url, **_kwargs):
            calls.append(url)
            if error_kind == "tls":
                raise httpx.ConnectError("hostname mismatch")
            return httpx.Response(503, request=httpx.Request("GET", url))

        async with AsyncHttpClient(transport=transport, resolver=PUBLIC, max_retries=3) as client:
            target = tmp_path / "fallback.tmp"
            if error_kind == "tls":
                with pytest.raises(httpx.ConnectError):
                    await client.download("https://public.example/image", target, max_retries=1)
                assert not target.exists()
            else:
                assert (await client.download("https://public.example/image", target, max_retries=1)).status_code == 503
            assert calls == ["https://public.example/image"]
            assert client.max_retries == 3

    asyncio.run(scenario())


@pytest.mark.parametrize("native", [False, True])
def test_sync_redirect_rejects_private_target_before_dispatch(monkeypatch, native):
    calls = []

    def response(url):
        calls.append(str(url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"}, request=httpx.Request("GET", url))

    if native:
        mock_native_stream(monkeypatch, lambda request: response(request.url))
        client = SafeHttpClient(resolver=PUBLIC)
    else:
        client = SafeHttpClient(transport=lambda url, **_kwargs: response(url), resolver=PUBLIC)
    with pytest.raises(UnsafeUrlError):
        client.get("https://public.example/start")
    assert calls == ["https://public.example/start"]


def test_sync_redirect_cookies_remain_on_original_host(monkeypatch):
    calls = []

    def handler(request):
        calls.append((str(request.url), request.headers.get("cookie")))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/next", "set-cookie": "unexpected=secret"})
        if request.url.path == "/next":
            return httpx.Response(307, headers={"location": "https://other.example/final"})
        return httpx.Response(200, content=b"ok")

    mock_native_stream(monkeypatch, handler)
    result = SafeHttpClient(resolver=PUBLIC).get("https://public.example/start", cookies={"age": "yes"})
    assert str(result.url) == "https://other.example/final"
    assert calls == [
        ("https://public.example/start", "age=yes"),
        ("https://public.example/next", "age=yes"),
        ("https://other.example/final", None),
    ]


def test_sync_redirect_count_is_bounded():
    calls = []

    def transport(url, **_kwargs):
        calls.append(url)
        return httpx.Response(302, headers={"location": "/loop"}, request=httpx.Request("GET", url))

    with pytest.raises(UnsafeUrlError, match="too many"):
        SafeHttpClient(transport=transport, resolver=PUBLIC).get("https://public.example/loop")
    assert len(calls) == 11


@pytest.mark.parametrize("declared_length", [None, "999"])
def test_sync_stream_cap_stops_reading_and_closes_response(monkeypatch, declared_length):
    read = []
    closed = []

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            for chunk in [b"123", b"456", b"789"]:
                read.append(chunk)
                yield chunk

        def close(self):
            closed.append(True)

    headers = {"Content-Length": declared_length} if declared_length else {}
    mock_native_stream(monkeypatch, lambda request: httpx.Response(200, headers=headers, stream=Body()))
    with pytest.raises(ResponseTooLarge):
        SafeHttpClient(resolver=PUBLIC, max_response_bytes=4).get("https://public.example/image")
    assert read == ([] if declared_length else [b"123", b"456"])
    assert closed == [True]


def test_sync_stream_preserves_decoded_content(monkeypatch):
    import gzip

    compressed = gzip.compress(b"original image bytes")
    mock_native_stream(monkeypatch, lambda request: httpx.Response(200, headers={"content-encoding": "gzip"}, content=compressed))
    result = SafeHttpClient(resolver=PUBLIC).get("https://public.example/image")
    assert result.content == b"original image bytes"
    assert result.text == "original image bytes"


def test_sync_injected_text_only_response_remains_compatible():
    def transport(url, **_kwargs):
        return SimpleNamespace(url=url, status_code=200, headers={}, content=b"", text="HTML")

    result = SafeHttpClient(transport=transport, resolver=PUBLIC).get("https://public.example/page")
    assert result.text == "HTML"
    with pytest.raises(ResponseTooLarge):
        SafeHttpClient(transport=transport, resolver=PUBLIC, max_response_bytes=3).get("https://public.example/page")
