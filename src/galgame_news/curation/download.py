"""Download candidates into a temporary staging area before ranking."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterable
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from PIL import Image

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


class ImageDownloader:
    def __init__(self, config: PrescanConfig, *, transport: Callable[..., Any] | None = None):
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

    def download(self, candidates: Iterable[ImageCandidate], directory: Path | str) -> tuple[list[ImageCandidate], list[FailureRecord]]:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        seen_urls: set[tuple[str, str]] = set()
        pending: list[ImageCandidate] = []
        filtered: list[FailureRecord] = []
        for candidate in candidates:
            parts = urlsplit(candidate.image_url.strip())
            normalized = urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), parts.path, parts.query, ""))
            key = (candidate.news_id, normalized)
            if key in seen_urls:
                continue
            seen_urls.add(key)
            placeholder_reason = placeholder_asset_reason(candidate)
            if placeholder_reason:
                filtered.append(self._failure(candidate, placeholder_reason, "placeholder asset was rejected before download", False))
                continue
            reason = meaningless_asset_reason(candidate)
            if reason or parts.scheme not in {"http", "https"}:
                filtered.append(self._failure(candidate, "filtered_invalid_material", reason or "invalid_scheme", False))
                continue
            pending.append(candidate)

        def fetch(candidate: ImageCandidate):
            try:
                response = self.client.get(candidate.image_url)
                status = int(getattr(response, "status_code", 200))
                if status < 200 or status >= 300:
                    return None, self._failure(candidate, "http_error", f"image request returned HTTP {status}", status >= 500 or status in {408, 429}), None
                final_response_url = str(getattr(response, "url", candidate.image_url) or candidate.image_url)
                redirected = candidate.model_copy(update={"image_url": final_response_url})
                if placeholder_asset_reason(redirected):
                    return None, self._failure(candidate, "placeholder_image", "redirected URL is a placeholder asset", False), None
                data = getattr(response, "content", b"") or b""
                declared_mime = getattr(response, "headers", {}).get("content-type")
                validation = self.validator.validate(data, declared_mime)
                if not validation.valid:
                    return None, self._failure(candidate, validation.reason or "invalid_image", "downloaded image failed validation", False), None
                suffix = _EXTENSIONS.get(validation.mime_type or "", ".img")
                target = root / f"{candidate.id}{suffix}"
                target.write_bytes(data)
                candidate.width, candidate.height = validation.width, validation.height
                candidate.mime_type, candidate.byte_size = validation.mime_type, len(data)
                candidate.sha256, candidate.perceptual_hash = hashlib.sha256(data).hexdigest(), _perceptual_hash(data)
                candidate.local_path, candidate.downloadable = str(target), True
                return candidate, None, final_response_url
            except Exception as exc:
                return None, self._failure(candidate, "download_error", str(exc), True), None

        accepted: list[ImageCandidate] = []
        failures: list[FailureRecord] = list(filtered)
        with ThreadPoolExecutor(max_workers=min(8, max(4, len(pending)))) as pool:
            outcomes = list(pool.map(fetch, pending))
        final_seen: set[tuple[str, str]] = set()
        root_resolved = root.resolve()
        for candidate, failure, final_url in outcomes:
            if failure is not None:
                failures.append(failure)
                continue
            if candidate is None:
                continue
            parts = urlsplit(final_url or candidate.image_url)
            final_key = (candidate.news_id, urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), parts.path, parts.query, "")))
            if final_key in final_seen:
                target = Path(candidate.local_path) if candidate.local_path else None
                if target and target.resolve().parent == root_resolved:
                    target.unlink(missing_ok=True)
                continue
            final_seen.add(final_key)
            accepted.append(candidate)
        return accepted, failures

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
