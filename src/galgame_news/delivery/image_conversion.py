"""Verified PNG/JPG review-image conversion while retaining source bytes."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps

from ..domain import ImageCandidate, ImageType


_FORMAT_MIME = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "GIF": "image/gif",
    "WEBP": "image/webp",
    "AVIF": "image/avif",
}


@dataclass(frozen=True)
class ConvertedImage:
    data: bytes
    mime_type: str
    width: int
    height: int
    original_mime_type: str
    animated: bool
    frame_index: int | None


class ImageConversionError(ValueError):
    """Source could not be decoded or encoded into a review image."""


class ImageConverter:
    """Convert decoded image media to a truthful, reviewable PNG or JPEG."""

    _LOSSLESS_TYPES = {
        ImageType.GAME_CG,
        ImageType.GAMEPLAY_SCREENSHOT,
        ImageType.KEY_VISUAL,
        ImageType.CHARACTER_ART,
        ImageType.ANNOUNCEMENT_ART,
        ImageType.COVER,
        ImageType.BANNER,
        ImageType.UI,
        ImageType.LOGO,
        ImageType.UNKNOWN,
    }

    def convert(self, source: bytes, candidate: ImageCandidate) -> ConvertedImage:
        try:
            with Image.open(BytesIO(source)) as opened:
                source_format = (opened.format or "").upper()
                source_mime = _FORMAT_MIME.get(source_format)
                if source_mime is None:
                    raise ImageConversionError(f"unsupported source encoding: {source_format or 'unknown'}")
                animated = int(getattr(opened, "n_frames", 1) or 1) > 1
                alpha = "A" in opened.getbands() or "transparency" in opened.info
                frame_index = 0 if animated else None
                if animated:
                    opened.seek(0)
                image = ImageOps.exif_transpose(opened.copy())
                has_orientation = bool(opened.getexif().get(274, 1) != 1)

                target_format = self._target_format(candidate, alpha, animated)
                already_compatible = (
                    target_format == source_format
                    and not animated
                    and not has_orientation
                    and (target_format == "PNG" or image.mode in {"RGB", "L"})
                )
                if already_compatible:
                    data = source
                else:
                    buffer = BytesIO()
                    if target_format == "JPEG":
                        image = image.convert("RGB")
                        image.save(buffer, format="JPEG", quality=95, optimize=True, progressive=True, subsampling=0)
                    else:
                        if alpha:
                            image = image.convert("RGBA")
                        elif image.mode not in {"RGB", "L"}:
                            image = image.convert("RGB")
                        image.save(buffer, format="PNG", optimize=True)
                    data = buffer.getvalue()

                # Re-open output bytes to ensure the extension and declared
                # MIME can be based on the actual encoded file.
                with Image.open(BytesIO(data)) as check:
                    actual_format = (check.format or "").upper()
                    check.load()
                    width, height = check.size
                if actual_format not in {"PNG", "JPEG"}:
                    raise ImageConversionError(f"converter produced unexpected encoding: {actual_format}")
                output_mime = _FORMAT_MIME[actual_format]
                return ConvertedImage(data, output_mime, width, height, source_mime, animated, frame_index)
        except ImageConversionError:
            raise
        except Exception as exc:
            raise ImageConversionError(f"image conversion failed ({type(exc).__name__})") from None

    def _target_format(self, candidate: ImageCandidate, alpha: bool, animated: bool) -> str:
        if alpha or animated or candidate.animated_source or candidate.image_type in self._LOSSLESS_TYPES:
            return "PNG"
        return "JPEG"
