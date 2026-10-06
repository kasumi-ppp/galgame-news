"""One task loop for HTTP, with bounded bridges for legacy parsers."""

from __future__ import annotations

import asyncio
from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import asdict
import time

from ..curation.download import ImageDownloader
from ..discovery.async_http import AsyncHttpClient
from ..discovery.async_resolver import resolve_async
from ..discovery.async_x_api import AsyncSocialDataTweetTransport
from .contracts import CancellationRequested


class SyncHttpBridge:
    """Only called from compatibility workers, never from the loop thread."""

    def __init__(self, runtime, client):
        self.runtime, self.client = runtime, client

    def get(self, url, *, cookies=None):
        return self.runtime.submit(self.client.get(url, cookies=cookies))

    def validate_url(self, url):
        self.client.validate_url(url)

    def __call__(self, url, **kwargs):
        header = kwargs.get("headers", {}).get("cookie", "")
        cookies = dict(part.strip().split("=", 1) for part in header.split(";") if "=" in part)
        return self.get(url, cookies=cookies)


class TaskNetworkRuntime:
    def __init__(self, runner, token):
        self.runner, self.token = runner, token
        self.loop = asyncio.get_running_loop()
        config, network = runner.config, runner.config.network
        self.page_client = AsyncHttpClient(
            transport=runner.source_transport, timeout=max(20.0, network.timeout_seconds),
            max_retries=network.max_retries, max_response_bytes=config.filters.max_response_bytes,
            user_agent=network.user_agent, concurrency=network.page_concurrency,
            per_host_concurrency=network.page_per_host, cancellation=token,
        )
        self.image_client = AsyncHttpClient(
            transport=runner.image_transport, timeout=network.timeout_seconds,
            max_retries=network.max_retries, max_response_bytes=config.filters.max_image_bytes,
            user_agent=network.user_agent, concurrency=network.image_concurrency,
            per_host_concurrency=network.image_per_host, cancellation=token,
        )
        self.socialdata = AsyncSocialDataTweetTransport(
            concurrency=network.socialdata_concurrency, cancellation=token,
        )
        self.page_bridge = SyncHttpBridge(self, self.page_client)
        self.compat = ThreadPoolExecutor(max_workers=network.page_concurrency, thread_name_prefix="weekly-source")
        self.processing = ThreadPoolExecutor(max_workers=network.image_processing_threads, thread_name_prefix="weekly-image")
        self._compat_slots = asyncio.Semaphore(network.page_concurrency)
        self.timings = {}
        self._active = set()

    async def call_sync(self, call):
        async with self._compat_slots:
            while self.token.is_paused and not self.token.is_cancelled:
                await asyncio.sleep(0.05)
            if self.token.is_cancelled:
                raise asyncio.CancelledError()
            return await self.loop.run_in_executor(self.compat, call)

    def submit(self, awaitable):
        async def tracked():
            task = asyncio.current_task()
            self._active.add(task)
            try:
                return await awaitable
            finally:
                self._active.discard(task)
        try:
            return asyncio.run_coroutine_threadsafe(tracked(), self.loop).result()
        except CancelledError:
            raise CancellationRequested("pipeline cancellation requested") from None

    async def resolve(self, resolver, news):
        return await self._timed("resolve", resolve_async(resolver, news, self.page_client, self.call_sync))

    async def collect_many(self, news, sources, request):
        async def collect(source):
            try:
                return await self.call_sync(lambda: self.runner._collect(news, source, request=request))
            except CancellationRequested:
                raise asyncio.CancelledError()
            except Exception as exc:
                return exc
        return await self._timed("collect", asyncio.gather(*(collect(source) for source in sources)))

    async def download(self, candidates, directory):
        downloader = ImageDownloader(self.runner.config)
        result = await self._timed("download_and_processing", downloader.download_async(
            candidates, directory, client=self.image_client, executor=self.processing,
            concurrency=self.runner.config.network.image_concurrency,
        ))
        self.timings["image_processing_worker_seconds"] = self.timings.get("image_processing_worker_seconds", 0.0) + downloader.processing_seconds
        return result

    def socialdata_lookup(self, status_url, *, token, timeout):
        return self.submit(self.socialdata.lookup(status_url, token=token, timeout=timeout))

    async def _timed(self, name, awaitable):
        start = time.monotonic()
        try:
            return await awaitable
        finally:
            self.timings[name] = self.timings.get(name, 0.0) + time.monotonic() - start

    def stats(self):
        return {
            "pages": asdict(self.page_client.stats),
            "images": asdict(self.image_client.stats),
            "socialdata_requests": self.socialdata.request_count,
            "stage_seconds": dict(self.timings),
        }

    async def cancel_pending(self):
        for task in list(self._active):
            task.cancel()
        await asyncio.gather(*list(self._active), return_exceptions=True)

    async def close(self):
        await self.cancel_pending()
        await self.page_client.close()
        await self.image_client.close()
        await self.socialdata.close()
        # Compatibility calls already drain before the coordinator returns.
        await asyncio.to_thread(self.compat.shutdown, wait=True, cancel_futures=True)
        await asyncio.to_thread(self.processing.shutdown, wait=True, cancel_futures=True)
