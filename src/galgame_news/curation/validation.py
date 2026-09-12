"""Downloaded image validation and semantic safety checks."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reason: str | None = None
    width: int | None = None
    height: int | None = None
    mime_type: str | None = None


MAGIC_MIME = {"jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}


class ImageValidator:
    def __init__(self, *, min_width: int = 300, min_height: int = 300, min_pixels: int = 120000, max_bytes: int = 12_582_912):
        self.min_width, self.min_height, self.min_pixels, self.max_bytes = min_width, min_height, min_pixels, max_bytes

    def validate(self, data: bytes, declared_mime: str | None = None) -> ValidationResult:
        if len(data) > self.max_bytes:
            return ValidationResult(False, "image_too_large")
        try:
            with Image.open(BytesIO(data)) as image:
                actual = MAGIC_MIME.get((image.format or "").casefold())
                if not actual:
                    return ValidationResult(False, "invalid_magic")
                if declared_mime and declared_mime.casefold().split(";", 1)[0].strip() != actual:
                    return ValidationResult(False, "mime_mismatch")
                image.verify()
            with Image.open(BytesIO(data)) as image:
                width, height = image.size
        except Exception:
            return ValidationResult(False, "corrupt_image")
        if width < self.min_width or height < self.min_height:
            return ValidationResult(False, "image_too_small", width, height, actual)
        if width * height < self.min_pixels:
            return ValidationResult(False, "pixel_count_too_small", width, height, actual)
        return ValidationResult(True, width=width, height=height, mime_type=actual)
