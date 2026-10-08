"""Bounded, isolated browser rendering for public discovery pages.

Playwright is an optional dependency. Browser traffic is routed through the
same ``AsyncHttpClient`` used by discovery so URL and response limits remain
in force; this module never opens a direct browser network connection.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import importlib.util
import re
from typing import Any
from urllib.parse import urlsplit

from ..config import BrowserConfig
from .async_http import AsyncHttpClient


@dataclass(frozen=True)
class BrowserSnapshot:
    html: str
    url: str


@dataclass(frozen=True)
class BrowserDiagnostic:
    code: str
    message: str


@dataclass(frozen=True)
class BrowserRenderResult:
    snapshots: tuple[BrowserSnapshot, ...] = ()
    diagnostics: tuple[BrowserDiagnostic, ...] = ()


@dataclass(frozen=True)
class BrowserRuntimeStatus:
    available: bool
    message: str


def check_browser_runtime() -> BrowserRuntimeStatus:
    """Check whether Playwright and its Chromium binary are already present."""

    try:
        if importlib.util.find_spec("playwright") is None:
            return BrowserRuntimeStatus(False, "未安装 Playwright 浏览器组件。")
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            if not playwright.chromium.executable_path:
                return BrowserRuntimeStatus(False, "Playwright Chromium 尚未安装。")
            from pathlib import Path

            if not Path(playwright.chromium.executable_path).is_file():
                return BrowserRuntimeStatus(False, "Playwright Chromium 尚未安装。")
        return BrowserRuntimeStatus(True, "Playwright 与 Chromium 已就绪。")
    except Exception:
        return BrowserRuntimeStatus(False, "Playwright 浏览器组件不可用，请在设置中检查或安装。")


class BrowserRenderer:
    """Render public HTML and capture bounded snapshots after gallery actions."""

    _BLOCKED_HOST_PARTS = (
        "google-analytics.com", "googletagmanager.com", "doubleclick.net",
        "facebook.net", "analytics.", "hotjar.com", "clarity.ms",
    )
    _ACTION_RE = re.compile(
        r"next|prev|previous|gallery|cg|image|photo|thumb|modal|lightbox|"
        r"次へ|前へ|画像|ギャラリー|CG|拡大|写真|年齢確認|18歳|18禁",
        re.I,
    )
    _SAFE_PUBLIC_COOKIE_RE = re.compile(r"^(?:age|adult|confirm|agecheck)[a-z0-9_-]*$", re.I)

    def __init__(
        self,
        client: AsyncHttpClient,
        config: BrowserConfig,
        cancellation: Any = None,
    ) -> None:
        self.client = client
        self.config = config
        self.cancellation = cancellation
        self._playwright: Any = None
        self._browser: Any = None
        self._contexts: dict[str, Any] = {}
        self._snapshots: dict[tuple[str, str], BrowserRenderResult] = {}
        self._flights: dict[tuple[str, str], asyncio.Task[BrowserRenderResult]] = {}
        self._pages_by_news: dict[str, int] = {}
        self._operations_by_news: dict[str, int] = {}
        self._cookies_by_news: dict[str, dict[str, str]] = {}
        self._lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(1)
        self._operations_by_page: dict[tuple[str, str], int] = {}
        self._requests_by_page: dict[tuple[str, str], int] = {}
        self._closed = False

    async def __aenter__(self) -> BrowserRenderer:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        self._closed = True
        pending = list(self._flights.values())
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for context in self._contexts.values():
            try:
                await context.close()
            except Exception:
                pass
        self._contexts.clear()
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def _cancelled(self) -> bool:
        return self._closed or bool(getattr(self.cancellation, "is_cancelled", False))

    def _diagnostic(self, code: str, message: str, snapshots: list[BrowserSnapshot]) -> BrowserRenderResult:
        return BrowserRenderResult(tuple(snapshots), (BrowserDiagnostic(code, message),))

    async def render(
        self,
        url: str,
        news_id: str,
        initial_html: str | None = None,
    ) -> BrowserRenderResult:
        """Render a URL once per news item, retaining useful partial snapshots."""

        if not self.config.enabled:
            return self._diagnostic("browser_disabled", "动态页面渲染已关闭。", [])
        if self._cancelled():
            return self._diagnostic("browser_cancelled", "动态页面渲染已取消。", [])
        await self._gate()
        key = (str(news_id), url)
        async with self._lock:
            if key in self._snapshots:
                return self._snapshots[key]
            task = self._flights.get(key)
            if task is None:
                task = asyncio.create_task(self._render_serial(url, str(news_id), initial_html))
                self._flights[key] = task
        try:
            try:
                result = await asyncio.shield(task)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    partial = await task
                    result = self._diagnostic(
                        "browser_timeout", "动态页面渲染超时，已保留已取得的页面快照。",
                        list(partial.snapshots),
                    )
                except Exception:
                    result = self._diagnostic("browser_timeout", "动态页面渲染超时。", [])
            if not any(d.code == "browser_cancelled" for d in result.diagnostics):
                async with self._lock:
                    self._snapshots[key] = result
            return result
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        finally:
            async with self._lock:
                if self._flights.get(key) is task and task.done():
                    self._flights.pop(key, None)

    async def _start(self) -> bool:
        if self._browser is not None:
            return True
        try:
            from playwright.async_api import async_playwright
        except (ImportError, ModuleNotFoundError):
            return False
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)
        return True

    async def _gate(self) -> None:
        while bool(getattr(self.cancellation, "is_paused", False)) and not self._cancelled():
            await asyncio.sleep(.05)
        if self._cancelled():
            raise asyncio.CancelledError()

    async def _render_serial(self, url, news_id, initial_html):
        async with self._slots:
            await self._gate()
            try:
                result = await asyncio.wait_for(self._render_one(url, news_id, initial_html), self.config.timeout_seconds)
                if not self._cancelled() and any(d.code == "browser_cancelled" for d in result.diagnostics):
                    return self._diagnostic("browser_timeout", "动态页面渲染超时，已保留页面快照。", list(result.snapshots))
                return result
            except asyncio.TimeoutError:
                return self._diagnostic("browser_timeout", "动态页面渲染超时。", [])

    async def _context(self, news_id: str) -> Any:
        context = self._contexts.get(news_id)
        if context is None:
            context = await self._browser.new_context(service_workers="block")
            if hasattr(context, "route_web_socket"):
                await context.route_web_socket("**/*", lambda socket: socket.close())
            context.on("page", lambda page: page.on("popup", lambda popup: asyncio.create_task(popup.close())))
            self._contexts[news_id] = context
            self._cookies_by_news.setdefault(news_id, {})
        return context

    async def _render_one(self, url: str, news_id: str, initial_html: str | None) -> BrowserRenderResult:
        snapshots: list[BrowserSnapshot] = []
        diagnostics: list[BrowserDiagnostic] = []
        page = None
        if self._cancelled():
            return self._diagnostic("browser_cancelled", "动态页面渲染已取消。", snapshots)
        async with self._lock:
            pages = self._pages_by_news.get(news_id, 0)
            if pages >= self.config.max_pages_per_news:
                return self._diagnostic("browser_page_limit", "该条新闻已达到浏览器页面上限。", snapshots)
            self._pages_by_news[news_id] = pages + 1
        try:
            if hasattr(self.client, "_validate_url"):
                await self.client._validate_url(url)
            elif hasattr(self.client, "validate_url"):
                self.client.validate_url(url)
            started = await asyncio.wait_for(self._start(), timeout=self.config.timeout_seconds)
            if not started:
                return self._diagnostic("browser_runtime_missing", "未安装 Playwright；请在设置中检查或显式安装浏览器组件。", snapshots)
            context = await asyncio.wait_for(self._context(news_id), timeout=self.config.timeout_seconds)
            page = await context.new_page()
            seen_html: set[str] = set()
            host = (urlsplit(url).hostname or "").casefold()

            async def capture() -> bool:
                if self._cancelled():
                    raise asyncio.CancelledError
                html = await page.content()
                final_url = page.url or url
                if html and html not in seen_html:
                    seen_html.add(html)
                    snapshots.append(BrowserSnapshot(html=html, url=final_url))
                    return True
                return False

            async def route_request(route: Any) -> None:
                request = route.request
                await self._gate()
                if self._cancelled():
                    await route.abort()
                    raise asyncio.CancelledError
                if request.method.upper() != "GET":
                    await route.abort()
                    return
                request_url = str(request.url)
                request_host = (urlsplit(request_url).hostname or "").casefold()
                resource_type = str(request.resource_type).casefold()
                request_host_text = request_host
                if (
                    resource_type in {"image", "media", "font", "websocket"}
                    or any(marker in request_host_text for marker in self._BLOCKED_HOST_PARTS)
                ):
                    await route.abort()
                    return
                request_key = (news_id, url)
                self._requests_by_page[request_key] = self._requests_by_page.get(request_key, 0) + 1
                if self._requests_by_page[request_key] > 64:
                    await route.abort()
                    diagnostics.append(BrowserDiagnostic("browser_resource_limit", "动态页面达到 64 个资源请求上限。"))
                    return
                if initial_html is not None and request.is_navigation_request() and request_url == url:
                    body = initial_html.encode("utf-8")
                    await route.fulfill(
                        status=200,
                        content_type="text/html; charset=utf-8",
                        body=body,
                    )
                    return
                cookies = self._cookies_by_news.setdefault(news_id + "|" + host, {}) if request_host == host else {}
                if request_host == host and hasattr(context, "cookies"):
                    for cookie in await context.cookies([request_url]):
                        name, value = str(cookie.get("name", "")), str(cookie.get("value", ""))
                        if (name == "PermitRate" and value == "18") or (self._SAFE_PUBLIC_COOKIE_RE.fullmatch(name) and value.casefold() in {"1", "true", "yes", "ok"}):
                            cookies[name] = value
                try:
                    response = await asyncio.wait_for(
                        self.client.get(request_url, cookies=cookies),
                        timeout=self.config.timeout_seconds,
                    )
                except asyncio.CancelledError:
                    await route.abort()
                    raise
                except Exception as exc:
                    await route.abort()
                    diagnostics.append(BrowserDiagnostic("browser_resource_failed", f"动态资源获取失败（{type(exc).__name__}）。"))
                    return
                if request_host == host:
                    self._remember_public_age_cookie(news_id, response.headers, request_host)
                if response.status_code >= 400:
                    diagnostics.append(BrowserDiagnostic("browser_resource_http_error", f"动态资源返回 HTTP {response.status_code}。"))
                headers = {
                    key: value for key, value in response.headers.items()
                    if key.casefold() not in {
                        "content-length", "content-encoding", "transfer-encoding",
                        "connection", "set-cookie",
                    }
                }
                try:
                    await route.fulfill(status=response.status_code, headers=headers, body=response.content)
                except Exception:
                    await route.abort()

            # Context routing also covers popup and frame requests.
            if hasattr(context, "route"):
                await context.route("**/*", route_request)
            else:
                await page.route("**/*", route_request)
            await page.goto(url, wait_until="domcontentloaded", timeout=round(self.config.timeout_seconds * 1000))
            await capture()
            for _ in range(3):
                if not await self._take_operation(news_id, url):
                    break
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(150)
                await capture()
            await self._click_gallery_controls(page, news_id, capture, url)
            await page.close()
            return BrowserRenderResult(tuple(snapshots), tuple(dict.fromkeys(diagnostics)))
        except asyncio.CancelledError:
            return self._diagnostic("browser_cancelled", "动态页面渲染已取消，已保留已取得的页面快照。", snapshots)
        except asyncio.TimeoutError:
            return self._diagnostic("browser_timeout", "动态页面渲染超时，已保留已取得的页面快照。", snapshots)
        except Exception as exc:
            missing = "executable doesn't exist" in str(exc).casefold()
            code = "browser_runtime_missing" if missing else "browser_render_failed"
            message = "Chromium 尚未安装，请在设置中安装浏览器组件。" if missing else f"动态页面渲染失败（{type(exc).__name__}），已保留页面快照。"
            return self._diagnostic(code, message, snapshots)
        finally:
            if page is not None and not getattr(page, "closed", False):
                try:
                    await page.close()
                except Exception:
                    pass

    async def _take_operation(self, news_id: str, url: str = "") -> bool:
        await self._gate()
        async with self._lock:
            key = (news_id, url)
            count = self._operations_by_page.get(key, 0)
            if count >= self.config.max_operations:
                return False
            self._operations_by_page[key] = count + 1
            self._operations_by_news[news_id] = self._operations_by_news.get(news_id, 0) + 1
            return True

    def _remember_public_age_cookie(self, news_id: str, headers: Any, host: str) -> None:
        raw = headers.get("set-cookie") or headers.get("Set-Cookie")
        if not raw:
            return
        first = str(raw).split(";", 1)[0]
        if "=" not in first:
            return
        name, value = first.split("=", 1)
        if (
            self._SAFE_PUBLIC_COOKIE_RE.fullmatch(name.strip())
            and value.strip().casefold() in {"1", "true", "yes", "ok"}
        ):
            self._cookies_by_news.setdefault(news_id + "|" + host, {})[name.strip()] = value.strip()

    async def _click_gallery_controls(self, page: Any, news_id: str, capture: Any, url: str = "") -> None:
        # Click only buttons and ARIA tabs whose accessible labels identify
        # gallery navigation. Anchors, forms, purchase controls, and arbitrary
        # page links are deliberately excluded.
        for selector in ("button", '[role="tab"]', '[role="button"]', '[class*="gallery_bt"]', '[data-gallery-target]'):
            try:
                locator = page.locator(selector)
                count = min(await locator.count(), 24)
            except Exception:
                continue
            for index in range(count):
                if self._cancelled():
                    return
                item = locator.nth(index)
                try:
                    if not await item.is_visible():
                        continue
                    tag = str(await item.evaluate("element => element.tagName.toLowerCase()"))
                    if tag in {"a", "form", "input", "select", "textarea"}:
                        continue
                    label = " ".join(
                        str(value or "") for value in (
                            await item.get_attribute("aria-label"),
                            await item.get_attribute("title"),
                            await item.inner_text(),
                            await item.get_attribute("class"),
                            await item.get_attribute("id"),
                        )
                    )
                    forbidden = re.search(r"cart|checkout|buy|purchase|login|submit|購入|カート|购买|購買", label, re.I)
                    if forbidden or not self._ACTION_RE.search(label) or not await self._take_operation(news_id, url):
                        continue
                    if await item.get_attribute("type") == "submit":
                        continue
                    advance = bool(re.search(r"next|次へ|次の|下一|次页|次頁", label, re.I))
                    if advance and not re.search(r"gallery|graphic|\bCG\b", label, re.I):
                        if not await item.evaluate("element => Boolean(element.closest('[data-gallery], .gallery, [id*=gallery], [id*=GALLERY]'))"):
                            continue
                    while True:
                        await item.click(timeout=min(1000, round(self.config.timeout_seconds * 1000)))
                        await page.wait_for_timeout(100)
                        changed = await capture()
                        if not advance or not changed or not await self._take_operation(news_id, url):
                            break
                except Exception:
                    continue


__all__ = [
    "BrowserDiagnostic", "BrowserRenderResult", "BrowserRenderer",
    "BrowserRuntimeStatus", "BrowserSnapshot", "check_browser_runtime",
]
