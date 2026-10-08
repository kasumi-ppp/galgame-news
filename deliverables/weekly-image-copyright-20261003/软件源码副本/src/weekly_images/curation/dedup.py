from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageChops, ImageStat

from ..domain import ImageCandidate, ImageCurationStatus, ReviewReason


@dataclass
class DedupResult:
    unique: list[ImageCandidate]
    duplicates: list[ImageCandidate]


def _original_hash(candidate: ImageCandidate) -> str | None:
    return candidate.original_sha256 or candidate.sha256


@lru_cache(maxsize=128)
def _comparison_pixels(path: str, modified_ns: int, byte_size: int) -> tuple[float, Image.Image] | None:
    """Cache bounded decoded comparisons, never full-size original pixels."""
    try:
        with Image.open(Path(path)) as source:
            source.seek(0)
            aspect = source.width / source.height
            pixels = source.convert("RGB")
            pixels.thumbnail((128, 128), Image.Resampling.LANCZOS)
            return aspect, pixels.resize((128, 128), Image.Resampling.LANCZOS)
    except (OSError, ValueError):
        return None


def _pixels(candidate: ImageCandidate) -> tuple[float, Image.Image] | None:
    """Load original content; metadata invalidates cache when a file changes."""
    path = candidate.original_path or candidate.local_path
    if not path:
        return None
    try:
        resolved = Path(path).resolve()
        stat = resolved.stat()
        return _comparison_pixels(str(resolved), stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError):
        return None


def _confirmed_visual_duplicate(left: ImageCandidate, right: ImageCandidate) -> bool:
    if not left.perceptual_hash or not right.perceptual_hash:
        return False
    try:
        distance = (int(left.perceptual_hash, 16) ^ int(right.perceptual_hash, 16)).bit_count()
    except ValueError:
        return False
    if distance > 8:
        return False
    a, b = _pixels(left), _pixels(right)
    if a is None or b is None:
        return False
    # A hash collision or two similarly composed CGs must not be enough.
    # Compare normalized decoded pixels too, allowing modest codec noise.
    if abs(a[0] - b[0]) > 0.01:
        return False
    difference = ImageChops.difference(a[1], b[1])
    mean_error = sum(ImageStat.Stat(difference).mean) / 3
    if mean_error > 3.0:
        return False
    # Codec noise is diffuse. A changed expression/pose is a concentrated
    # patch and must not be merged just because most of the background agrees.
    channels = difference.split()
    maximum = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    changed_fraction = sum(maximum.histogram()[17:]) / (128 * 128)
    if changed_fraction > 0.025:
        return False
    for y in range(0, 128, 8):
        for x in range(0, 128, 8):
            tile_error = sum(ImageStat.Stat(difference.crop((x, y, x + 8, y + 8))).mean) / 3
            if tile_error > 8.0:
                return False
    return True


def _representative_key(candidate: ImageCandidate) -> tuple:
    # Detail evidence comes before rank score and encoded file size. Original
    # dimensions are used when present; byte size is deliberately ignored.
    detail = float(candidate.signals.get("visible_detail_score", candidate.signals.get("sharpness_score", 0.0)) or 0.0)
    width = candidate.original_width or candidate.width or 0
    height = candidate.original_height or candidate.height or 0
    return (
        not (candidate.signals.get("x_api_photo") is True or candidate.signals.get("socialdata_photo") is True),
        candidate.signals.get("auto_select", True) is False,
        candidate.signals.get("entity_match") is not True,
        -detail,
        -min(width, 1280) * min(height, 720),
        -(candidate.score.total if candidate.score else 0.0),
        candidate.id or "",
    )


class Deduplicator:
    def deduplicate(self, candidates: Iterable[ImageCandidate], *, historical_hashes: dict[str, str] | None = None) -> DedupResult:
        unique: list[ImageCandidate] = []
        duplicates: list[ImageCandidate] = []
        historical_hashes = historical_hashes or {}
        groups: dict[str, list[ImageCandidate]] = {}
        for candidate in candidates:
            groups.setdefault(candidate.news_id, []).append(candidate)
        for group in groups.values():
            representatives: list[ImageCandidate] = []
            for candidate in sorted(group, key=_representative_key):
                if candidate.signals.get("fallback_old_material") and ReviewReason.FALLBACK_OLD_MATERIAL not in candidate.review_reasons:
                    candidate.review_reasons.append(ReviewReason.FALLBACK_OLD_MATERIAL)
                sha = _original_hash(candidate)
                historical = (sha and sha in historical_hashes) or (candidate.perceptual_hash and candidate.perceptual_hash in historical_hashes.values())
                if historical and ReviewReason.HISTORICAL_DUPLICATE not in candidate.review_reasons:
                    candidate.review_reasons.append(ReviewReason.HISTORICAL_DUPLICATE)

                duplicate_of = next((rep for rep in representatives if sha and sha == _original_hash(rep)), None)
                duplicate_kind = "sha256" if duplicate_of is not None else None
                if duplicate_of is None:
                    duplicate_of = next((rep for rep in representatives if _confirmed_visual_duplicate(candidate, rep)), None)
                    if duplicate_of is not None:
                        duplicate_kind = "perceptual_pixels"
                if duplicate_of is not None:
                    candidate.selected = False
                    candidate.curation_status = ImageCurationStatus.UNSELECTED
                    candidate.signals["duplicate_of"] = str(duplicate_of.id or "")
                    candidate.signals["duplicate_kind"] = duplicate_kind or "sha256"
                    candidate.signals["duplicate_reason"] = "same_news_same_scene"
                    if "duplicate_of_better_candidate" not in candidate.selection_reasons:
                        candidate.selection_reasons.append("duplicate_of_better_candidate")
                    duplicates.append(candidate)
                    continue
                possible = next((rep for rep in representatives if candidate.perceptual_hash and candidate.perceptual_hash == rep.perceptual_hash), None)
                if possible is not None:
                    candidate.signals["possible_duplicate_of"] = str(possible.id or "")
                else:
                    candidate.signals.pop("possible_duplicate_of", None)
                candidate.signals.pop("duplicate_of", None)
                candidate.signals.pop("duplicate_kind", None)
                candidate.signals.pop("duplicate_reason", None)
                representatives.append(candidate)
                unique.append(candidate)
        return DedupResult(unique=unique, duplicates=duplicates)
