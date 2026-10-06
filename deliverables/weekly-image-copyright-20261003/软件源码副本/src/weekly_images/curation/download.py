"""Download candidates into a temporary staging area before ranking."""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
import time
from threading import Lock
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterable
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from PIL import Image, ImageFilter

from ..config import PrescanConfig
from ..domain import FailureRecord, FailureStage, ImageCandidate
from ..discovery.http import SafeHttpClient
from .validation import ImageValidator, meaningless_asset_reason, placeholder_asset_reason


_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/avif": ".avif",
}


def _perceptual_hash(data: bytes) -> str | None:
    try:
        with Image.open(BytesIO(data)) as image:
            grayscale = image.convert("L").resize((8, 8), Image.Resampling.LANCZOS)
            values = list(grayscale.get_flattened_data() if hasattr(grayscale, "get_flattened_data") else grayscale.getdata())
        average = sum(values) / len(values)
        bits = "".join("1" if value >= average else "0" for value in values)
        return f"{int(bits, 2):016x}"
    except Exception:
        return None


def _image_quality_signals(data: bytes, width: int, height: int) -> dict[str, float | bool | str]:
    """Measure source detail, blur, and blockiness without using byte size."""
    try:
        with Image.open(BytesIO(data)) as source:
            frame_count = int(getattr(source, "n_frames", 1) or 1)
            has_alpha = "A" in source.getbands() or "transparency" in source.info
            frame = source.copy().convert("L")
            frame.thumbnail((384, 384), Image.Resampling.LANCZOS)
            edge_histogram = frame.filter(ImageFilter.FIND_EDGES).histogram()
            count = max(1, sum(edge_histogram))
            edge_energy = sum(index * amount for index, amount in enumerate(edge_histogram)) / count / 255.0
            pixels = frame.load()
            frame_width, frame_height = frame.size
            second_derivatives: list[int] = []
            boundary_diffs: list[int] = []
            non_boundary_diffs: list[int] = []
            for y in range(1, frame_height - 1):
                for x in range(1, frame_width - 1):
                    center = pixels[x, y]
                    laplace = abs(4 * center - pixels[x - 1, y] - pixels[x + 1, y] - pixels[x, y - 1] - pixels[x, y + 1])
                    second_derivatives.append(laplace)
                    horizontal = abs(center - pixels[x - 1, y])
                    vertical = abs(center - pixels[x, y - 1])
                    if x % 8 == 0:
                        boundary_diffs.append(horizontal)
                    elif x % 8 == 1:
                        non_boundary_diffs.append(horizontal)
                    if y % 8 == 0:
                        boundary_diffs.append(vertical)
                    elif y % 8 == 1:
                        non_boundary_diffs.append(vertical)
            laplace_energy = sum(second_derivatives) / max(1, len(second_derivatives)) / 1020.0
            blockiness = max(0.0, min(1.0, (
                sum(boundary_diffs) / max(1, len(boundary_diffs))
                - sum(non_boundary_diffs) / max(1, len(non_boundary_diffs))
            ) / 255.0 * 4.0))
            # These are comparative ranking signals, not calibrated quality
            # labels. Preserve raw pixels and validate thresholds offline.
            sharpness = max(0.0, min(1.0, 0.55 * edge_energy * 2.8 + 0.45 * laplace_energy * 8.0))
            resolution = max(0.0, min(1.0, min(width / 1280.0, height / 720.0)))
            detail = max(0.0, min(1.0, 0.65 * resolution + 0.35 * sharpness - 0.15 * blockiness))
            return {
                "sharpness_score": round(sharpness, 6),
                "visible_detail_score": round(detail, 6),
                "blur_score": round(max(0.0, min(1.0, 1.0 - laplace_energy * 8.0)), 6),
                "blockiness_score": round(blockiness, 6),
                "transparent": has_alpha,
                "animated_source": frame_count > 1,
                "animation_frame_count": frame_count,
            }
    except Exception:
        return {"quality_analysis_failed": True}


class ImageDownloader:
    def __init__(self, config: PrescanConfig, *, transport: Callable[..., Any] | None = None):
        self.processing_seconds = 0.0
        self._timing_lock = Lock()
        self.client = SafeHttpClient(
            transport=transport,
            timeout=config.network.timeout_seconds,
            max_retries=config.network.max_retries,
            max_response_bytes=config.filters.max_image_bytes,
            user_agent=config.network.user_agent,
        )
        self.validator = ImageValidator(
            min_width=config.filters.min_width,
            min_height=config.filters.min_height,
            min_pixels=config.filters.min_pixels,
            max_bytes=config.filters.max_image_bytes,
        )

    def _prepare(self, candidates, directory):
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        seen_urls: set[tuple[str, str]] = set()
        candidate_order: list[str] = []
        duplicate_urls: dict[tuple[str, str], ImageCandidate] = {}
        pending: list[ImageCandidate] = []
        filtered: list[FailureRecord] = []
        invalid: list[ImageCandidate] = []
        for candidate in candidates:
            parts = urlsplit(candidate.image_url.strip())
            normalized = urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), parts.path, parts.query, ""))
            key = (candidate.news_id, normalized)
            if key in seen_urls:
                original = duplicate_urls[key]
                alternate_roots = [str(original.signals.get("root_source_url", "")), str(candidate.signals.get("root_source_url", ""))]
                roots = list(dict.fromkeys(value for value in alternate_roots if value))
                if roots:
                    original.signals["source_chain_alternates"] = "|".join(roots)
                continue
            seen_urls.add(key)
            duplicate_urls[key] = candidate
            candidate_order.append(str(candidate.id))
            placeholder_reason = placeholder_asset_reason(candidate)
            if placeholder_reason:
                candidate.downloadable = False
                candidate.signals["invalid_reason"] = placeholder_reason
                invalid.append(candidate)
                filtered.append(self._failure(candidate, placeholder_reason, "placeholder asset was rejected before download", False))
                continue
            reason = meaningless_asset_reason(candidate)
            if reason or parts.scheme not in {"http", "https"}:
                failure_code = "filtered_invalid_material"
                candidate.downloadable = False
                candidate.signals["invalid_reason"] = reason or "invalid_scheme"
                invalid.append(candidate)
                filtered.append(self._failure(candidate, failure_code, reason or "invalid_scheme", False))
                continue
            pending.append(candidate)

        return root, candidate_order, pending, filtered, invalid

    def _process_response(self, candidate, response, root, temporary=None):
        started = time.monotonic()
        try:
            status = int(getattr(response, "status_code", 200))
            if status < 200 or status >= 300:
                return candidate, self._failure(candidate, "http_error", f"image request returned HTTP {status}", status >= 500 or status in {408, 429}), None
            final_response_url = str(getattr(response, "url", candidate.image_url) or candidate.image_url)
            redirected = candidate.model_copy(update={"image_url": final_response_url})
            if placeholder_asset_reason(redirected):
                return candidate, self._failure(candidate, "placeholder_image", "redirected URL is a placeholder asset", False), None
            data = temporary.read_bytes() if temporary is not None else (getattr(response, "content", b"") or b"")
            declared_mime = getattr(response, "headers", {}).get("content-type")
            validation = self.validator.validate(data, declared_mime)
            if not validation.valid:
                return candidate, self._failure(candidate, validation.reason or "invalid_image", "downloaded image failed validation", False), None
            suffix = _EXTENSIONS.get(validation.mime_type or "", ".img")
            target = root / f"{candidate.id}{suffix}"
            if temporary is None:
                temporary = root / f".{candidate.id}-{uuid.uuid4().hex}.part"
                temporary.write_bytes(data)
            os.replace(temporary, target)
            candidate.width, candidate.height = validation.width, validation.height
            candidate.mime_type, candidate.byte_size = validation.mime_type, len(data)
            candidate.sha256, candidate.perceptual_hash = hashlib.sha256(data).hexdigest(), _perceptual_hash(data)
            candidate.local_path, candidate.downloadable = str(target), True
            candidate.original_path = str(target)
            candidate.original_mime_type = validation.mime_type
            candidate.original_byte_size = len(data)
            candidate.original_sha256 = candidate.sha256
            candidate.original_width, candidate.original_height = validation.width, validation.height
            candidate.signals.update(_image_quality_signals(data, validation.width or 0, validation.height or 0))
            return candidate, None, final_response_url
        except Exception as exc:
            return candidate, self._failure(candidate, "download_error", str(exc), True), None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            with self._timing_lock:
                self.processing_seconds += time.monotonic() - started


    def _finish(self, outcomes, candidate_order, filtered, invalid):
        accepted: list[ImageCandidate] = []
        failures: list[FailureRecord] = list(filtered)
        for candidate, failure, final_url in outcomes:
            if failure is not None:
                if candidate is not None:
                    candidate.downloadable = False
                    candidate.local_path = None
                    candidate.signals["invalid_reason"] = failure.code
                    invalid.append(candidate)
                failures.append(failure)
                continue
            if candidate is None:
                continue
            accepted.append(candidate)
        # Keep invalid and downloaded candidate records. Failures are attached
        # separately for diagnostics; callers can still account for every URL.
        by_id = {str(candidate.id): candidate for candidate in [*accepted, *invalid]}
        all_candidates = [by_id[candidate_id] for candidate_id in candidate_order if candidate_id in by_id]
        return all_candidates, failures

    def download(self, candidates, directory):
        root, order, pending, filtered, invalid = self._prepare(candidates, directory)
        def fetch(candidate):
            try:
                response = self.client.get(candidate.image_url)
                return self._process_response(candidate, response, root)
            except Exception as exc:
                return candidate, self._failure(candidate, "download_error", str(exc), True), None
        with ThreadPoolExecutor(max_workers=min(8, max(4, len(pending)))) as pool:
            outcomes = list(pool.map(fetch, pending))
        return self._finish(outcomes, order, filtered, invalid)

    async def download_async(self, candidates, directory, *, client, executor, concurrency=8):
        """Stream with a bounded window; CPU processing never runs on the loop."""
        root, order, pending, filtered, invalid = self._prepare(candidates, directory)
        loop = asyncio.get_running_loop()
        outcomes = [None] * len(pending)
        queue = asyncio.Queue()
        for index, candidate in enumerate(pending):
            queue.put_nowait((index, candidate))

        async def worker():
            while not queue.empty():
                index, candidate = queue.get_nowait()
                temporary = root / f".{candidate.id}-{uuid.uuid4().hex}.part"
                try:
                    response = await client.download(candidate.image_url, temporary)
                    # At most concurrency downloaded files can wait for the
                    # limited processing executor, including files in flight.
                    processing = loop.run_in_executor(executor, self._process_response, candidate, response, root, temporary)
                    try:
                        outcomes[index] = await asyncio.shield(processing)
                    except asyncio.CancelledError:
                        await processing
                        raise
                except Exception as exc:
                    outcomes[index] = candidate, self._failure(candidate, "download_error", str(exc), True), None
                finally:
                    temporary.unlink(missing_ok=True)
                    queue.task_done()

        tasks = [asyncio.create_task(worker()) for _ in range(min(concurrency, len(pending)))]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return self._finish(outcomes, order, filtered, invalid)

    @staticmethod
    def _failure(candidate: ImageCandidate, code: str, message: str, retryable: bool) -> FailureRecord:
        return FailureRecord(
            stage=FailureStage.DOWNLOAD,
            news_id=candidate.news_id,
            candidate_id=candidate.id,
            code=code,
            message=message,
            source_url=candidate.image_url,
            retryable=retryable,
        )
