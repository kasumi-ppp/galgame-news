from __future__ import annotations

import asyncio
import sys
import types
import pytest

from galgame_news.config import BrowserConfig
from galgame_news.discovery.async_http import AsyncHttpResponse
from galgame_news.discovery.browser import BrowserRenderer


class FakeRequest:
    method = "GET"
    resource_type = "document"

    def __init__(self, url: str, *, navigation: bool = True, resource_type: str = "document"):
        self.url = url
        self._navigation = navigation
        self.resource_type = resource_type

    def is_navigation_request(self) -> bool:
        return self._navigation


class FakeRoute:
    def __init__(self, request: FakeRequest):
        self.request = request
        self.fulfilled: dict | None = None
        self.aborted = False

    async def fulfill(self, **values):
        self.fulfilled = values

    async def abort(self):
        self.aborted = True


class FakeLocator:
    async def count(self):
        return 0


class FakePage:
    def __init__(self, *, delay: float = 0):
        self.route_handler = None
        self.url = "about:blank"
        self.html = ""
        self.routes: list[FakeRoute] = []
        self.delay = delay
        self.closed = False

    async def route(self, _pattern, handler):
        self.route_handler = handler

    async def goto(self, url, **_kwargs):
        self.url = url
        if self.delay:
            await asyncio.sleep(self.delay)
        route = FakeRoute(FakeRequest(url))
        self.routes.append(route)
        await self.route_handler(route)
        if route.fulfilled:
            self.html = route.fulfilled["body"].decode("utf-8")

    async def content(self):
        return self.html

    async def evaluate(self, _script):
        return None

    async def wait_for_timeout(self, _milliseconds):
        return None

    def locator(self, _selector):
        return FakeLocator()

    async def close(self):
        self.closed = True


class FakeContext:
    def __init__(self, page: FakePage):
        self.page = page

    async def new_page(self):
        return self.page

    async def close(self):
        return None


class FakeClient:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url, *, cookies=None):
        self.calls.append((url, dict(cookies or {})))
        return AsyncHttpResponse(b"<html><body>remote</body></html>", 200, {"content-type": "text/html"}, url)


def fake_renderer(client: FakeClient, config: BrowserConfig, page: FakePage | None = None, cancellation=None):
    renderer = BrowserRenderer(client, config, cancellation)
    context = FakeContext(page or FakePage())

    async def start():
        return True

    async def get_context(_news_id):
        return context

    renderer._start = start
    renderer._context = get_context
    return renderer, context.page


def test_initial_html_is_reused_without_http_refetch():
    async def run():
        client = FakeClient()
        renderer, page = fake_renderer(client, BrowserConfig())
        result = await renderer.render("https://example.org/news", "n1", "<html>initial</html>")
        assert client.calls == []
        assert [snapshot.html for snapshot in result.snapshots] == ["<html>initial</html>"]
        assert result.snapshots[0].url == "https://example.org/news"
        assert page.closed

    asyncio.run(run())


def test_requests_use_injected_client_and_block_image_resources():
    async def run():
        client = FakeClient()
        renderer, page = fake_renderer(client, BrowserConfig())
        result = await renderer.render("https://example.org/news", "n1")
        image_route = FakeRoute(FakeRequest("https://example.org/photo.jpg", navigation=False, resource_type="image"))
        await page.route_handler(image_route)
        assert result.snapshots
        assert client.calls == [("https://example.org/news", {})]
        assert image_route.aborted

    asyncio.run(run())


def test_page_and_operation_limits_are_per_news_item():
    async def run():
        client = FakeClient()
        renderer, _page = fake_renderer(client, BrowserConfig(max_pages_per_news=1, max_operations=1))
        first = await renderer.render("https://example.org/one", "n1", "<html>one</html>")
        second = await renderer.render("https://example.org/two", "n1", "<html>two</html>")
        assert len(first.snapshots) == 1
        assert second.diagnostics[0].code == "browser_page_limit"
        assert renderer._operations_by_news["n1"] == 1
        assert second.diagnostics[0].message

    asyncio.run(run())


def test_missing_playwright_returns_diagnostic_without_installing():
    async def run():
        client = FakeClient()
        renderer, _page = fake_renderer(client, BrowserConfig())

        async def missing_runtime():
            return False

        renderer._start = missing_runtime
        result = await renderer.render("https://example.org/news", "n1")
        assert result.snapshots == ()
        assert result.diagnostics[0].code == "browser_runtime_missing"
        assert result.diagnostics[0].message
        assert client.calls == []

    asyncio.run(run())


def test_timeout_and_cooperative_cancellation_return_chinese_diagnostics():
    async def run():
        client = FakeClient()
        slow_page = FakePage(delay=0.2)
        renderer, _ = fake_renderer(client, BrowserConfig(timeout_seconds=0.02), slow_page)
        timeout_result = await renderer.render("https://example.org/slow", "n1")
        assert timeout_result.diagnostics[0].code == "browser_timeout"
        assert timeout_result.diagnostics[0].message

        class Cancelled:
            is_cancelled = True

        cancelled_renderer, _ = fake_renderer(client, BrowserConfig(), cancellation=Cancelled())
        cancelled_result = await cancelled_renderer.render("https://example.org/news", "n2")
        assert cancelled_result.diagnostics[0].code == "browser_cancelled"
        assert cancelled_result.diagnostics[0].message

    asyncio.run(run())


def test_runtime_missing_import_is_reported_without_auto_install(monkeypatch):
    async def run():
        client = FakeClient()
        renderer = BrowserRenderer(client, BrowserConfig())
        monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
        monkeypatch.setitem(sys.modules, "playwright.async_api", None)
        assert await renderer._start() is False

    asyncio.run(run())
