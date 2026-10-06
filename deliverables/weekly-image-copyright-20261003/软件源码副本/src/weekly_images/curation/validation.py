"""Downloaded image validation and semantic safety checks."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import re
from urllib.parse import unquote, urlsplit

from PIL import Image

from ..domain import ImageCandidate


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reason: str | None = None
    width: int | None = None
    height: int | None = None
    mime_type: str | None = None


MAGIC_MIME = {"jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp", "avif": "image/avif"}

_PLACEHOLDER_TERMS = (
    "now_printing", "now-printing", "no_image", "no-image", "noimage",
    "placeholder", "dummy", "image_pending", "image-pending", "coming_soon",
    "coming-soon", "preparing", "準備中", "画像準備中", "画像なし",
)


def placeholder_asset_reason(candidate: ImageCandidate) -> str | None:
    """Return a stable failure code for known placeholder assets."""
    value = unquote(urlsplit(candidate.image_url).path + "?" + urlsplit(candidate.image_url).query).casefold()
    compact = re.sub(r"[^a-z0-9一-龯ぁ-んァ-ヶ]+", "", value)
    for term in _PLACEHOLDER_TERMS:
        folded = term.casefold()
        if folded in value or re.sub(r"[^a-z0-9一-龯ぁ-んァ-ヶ]+", "", folded) in compact:
            return "placeholder_image"
    return None


def meaningless_asset_reason(candidate: ImageCandidate) -> str | None:
    """Reject non-image assets, not files whose semantic type may be useful."""
    path = urlsplit(candidate.image_url).path.casefold()
    if path.endswith(".svg") or "profile_images" in path or "editorui/fonts" in path:
        return "invalid_material"
    return None


class ImageValidator:
    def __init__(self, *, min_width: int = 300, min_height: int = 300, min_pixels: int = 120000, max_bytes: int = 12_582_912):
        self.min_width, self.min_height, self.min_pixels, self.max_bytes = min_width, min_height, min_pixels, max_bytes

    def validate(self, data: bytes, declared_mime: str | None = None) -> ValidationResult:
        if len(data) > self.max_bytes:
            return ValidationResult(False, "image_too_large")
        actual: str | None = None
        try:
            with Image.open(BytesIO(data)) as image:
                actual = MAGIC_MIME.get((image.format or "").casefold())
                if (image.format or "").casefold() == "avif":
                    actual = "image/avif"
                if not actual:
                    return ValidationResult(False, "invalid_magic")
                image.verify()
            with Image.open(BytesIO(data)) as image:
                width, height = image.size
                image.load()
        except Exception:
            return ValidationResult(False, "corrupt_image")
        if width < 16 or height < 16:
            return ValidationResult(False, "tracking_pixel", width, height, actual)
        return ValidationResult(True, width=width, height=height, mime_type=actual)
