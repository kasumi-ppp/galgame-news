import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import threading
import time

from PIL import Image

from galgame_news.config import load_config
from galgame_news.curation.download import ImageDownloader
from galgame_news.discovery.async_http import AsyncHttpClient
from galgame_news.domain import ImageCandidate, SourceType


def test_download_window_and_processing_pool_are_bounded_and_preserve_records(tmp_path, monkeypatch):
    import galgame_news.curation.download as module
    active, maximum = [0, 0], [0, 0]
    lock = threading.Lock()
    image = BytesIO()
    Image.new("RGB", (800, 600), "red").save(image, format="JPEG")
    original_quality = module._image_quality_signals
    def quality(*args):
        with lock:
            active[1] += 1
            maximum[1] = max(maximum[1], active[1])
        try:
            time.sleep(0.03)
            return original_quality(*args)
        finally:
            with lock:
                active[1] -= 1
    monkeypatch.setattr(module, "_image_quality_signals", quality)
    async def transport(url, **kwargs):
        active[0] += 1
        maximum[0] = max(maximum[0], active[0])
        try:
            await asyncio.sleep(0.01)
            content = b"invalid" if url.endswith("9.jpg") else image.getvalue()
            return type("Response", (), {"url":url, "content":content, "headers":{"content-type":"image/jpeg"}, "status_code":200})()
        finally:
            active[0] -= 1
    candidates = [ImageCandidate(news_id="n1", image_url=f"https://images.example/{index}.jpg", source_url="https://site.example/gallery", source_type=SourceType.OFFICIAL_SITE, fetched_at=datetime.now(timezone.utc)) for index in range(10)]
    async def run():
        async with AsyncHttpClient(transport=transport, resolver=lambda _: ["93.184.216.34"], concurrency=3, per_host_concurrency=3) as client:
            with ThreadPoolExecutor(max_workers=2) as executor:
                result, failures = await ImageDownloader(load_config()).download_async(candidates, tmp_path, client=client, executor=executor, concurrency=3)
            assert result == candidates
            assert len(failures) == 1
            assert result[-1].downloadable is False
            assert maximum[0] <= 3 and maximum[1] <= 2
            assert maximum[0] > 1 and maximum[1] == 2
            assert not list(tmp_path.glob("*.part"))
            for candidate in result[:-1]:
                assert candidate.original_sha256 == candidate.sha256
                with Image.open(candidate.original_path) as decoded:
                    assert decoded.format == "JPEG"
    asyncio.run(run())
