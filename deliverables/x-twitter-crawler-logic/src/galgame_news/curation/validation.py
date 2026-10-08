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


MAGIC_MIME = {"jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}

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
    path = urlsplit(candidate.image_url).path.casefold()
    if path.endswith(".svg") or "profile_images" in path:
        return "invalid_material"
    tokens = set(filter(None, re.split(r"[^a-z0-9]+", path)))
    meaningless = {"logo", "favicon", "icon", "icons", "sprite", "button", "btn", "banner", "header", "footer", "thumbnail", "thumb", "capsule", "fonts", "editorui", "parastorage"}
    return "meaningless_asset" if tokens & meaningless else None


def _is_avif(data: bytes) -> bool:
    return len(data) >= 16 and data[4:8] == b"ftyp" and (data[8:12] in {b"avif", b"avis"} or b"avif" in data[8:32])


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
                if not actual:
                    return ValidationResult(False, "invalid_magic")
                if declared_mime and declared_mime.casefold().split(";", 1)[0].strip() != actual:
                    return ValidationResult(False, "mime_mismatch")
                image.verify()
            with Image.open(BytesIO(data)) as image:
                width, height = image.size
        except Exception:
            if _is_avif(data) and (not declared_mime or declared_mime.casefold().split(";", 1)[0].strip() == "image/avif"):
                return ValidationResult(True, mime_type="image/avif")
            return ValidationResult(False, "corrupt_image")
        if width < self.min_width or height < self.min_height:
            return ValidationResult(False, "image_too_small", width, height, actual)
        if width * height < self.min_pixels:
            return ValidationResult(False, "pixel_count_too_small", width, height, actual)
        return ValidationResult(True, width=width, height=height, mime_type=actual)
