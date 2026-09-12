"""Download candidates into a temporary staging area before ranking."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from io import BytesIO
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from ..config import PrescanConfig
from ..domain import FailureRecord, FailureStage, ImageCandidate
from ..discovery.http import SafeHttpClient
from .validation import ImageValidator, meaningless_asset_reason


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
        accepted: list[ImageCandidate] = []
        failures: list[FailureRecord] = []
        seen_urls: set[tuple[str, str]] = set()
        for candidate in candidates:
            key = (candidate.news_id, candidate.image_url)
            if key in seen_urls:
                continue
            seen_urls.add(key)
            semantic_reason = meaningless_asset_reason(candidate)
            if semantic_reason:
                failures.append(self._failure(candidate, semantic_reason, "filtered non-content image", False))
                continue
            try:
                response = self.client.get(candidate.image_url)
                status = int(getattr(response, "status_code", 200))
                if status < 200 or status >= 300:
                    failures.append(self._failure(candidate, "http_error", f"image request returned HTTP {status}", status >= 500 or status in {408, 429}))
                    continue
                data = getattr(response, "content", b"") or b""
                declared_mime = getattr(response, "headers", {}).get("content-type")
                validation = self.validator.validate(data, declared_mime)
                if not validation.valid:
                    failures.append(self._failure(candidate, validation.reason or "invalid_image", "downloaded image failed validation", False))
                    continue
                suffix = _EXTENSIONS.get(validation.mime_type or "", ".img")
                target = root / f"{candidate.id}{suffix}"
                target.write_bytes(data)
                candidate.width = validation.width
                candidate.height = validation.height
                candidate.mime_type = validation.mime_type
                candidate.byte_size = len(data)
                candidate.sha256 = hashlib.sha256(data).hexdigest()
                candidate.perceptual_hash = _perceptual_hash(data)
                candidate.local_path = str(target)
                candidate.downloadable = True
                accepted.append(candidate)
            except Exception as exc:
                failures.append(self._failure(candidate, "download_error", str(exc), True))
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
