"""Keep useful alternatives visible without discarding source assets."""

from pathlib import Path
from typing import Iterable

from ..domain import ImageCandidate
from .dedup import _confirmed_visual_duplicate, _original_hash, _representative_key


def is_low_resolution(candidate: ImageCandidate) -> bool:
    """Use native dimensions; missing dimensions are not proof of low quality."""
    width = candidate.original_width or candidate.width or 0
    height = candidate.original_height or candidate.height or 0
    return bool(width > 0 and height > 0 and (
        max(width, height) < 800 or min(width, height) < 600
    ))


def _available(candidate: ImageCandidate) -> bool:
    for value in (candidate.local_path, candidate.original_path):
        try:
            if value and Path(value).is_file():
                return True
        except (OSError, ValueError):
            continue
    return False


def _same_scene(left: ImageCandidate, right: ImageCandidate) -> bool:
    if left.news_id != right.news_id:
        return False
    left_hash, right_hash = _original_hash(left), _original_hash(right)
    return bool(left_hash and left_hash == right_hash) or _confirmed_visual_duplicate(left, right)


def reviewable_pending_images(
    pending: Iterable[ImageCandidate], *, accepted: Iterable[ImageCandidate] = ()
) -> list[ImageCandidate]:
    """Filter review/export only, leaving decisions, indices and originals intact.

    Accepted images take precedence. Among alternatives use the existing native
    detail ranking and confirm duplicates with hashes plus decoded pixels. Never
    suppress an alternative solely because a stale duplicate target is recorded.
    """
    values = list(pending)
    representatives = [candidate for candidate in accepted if _available(candidate)]
    retained: set[int] = set()
    for candidate in sorted(values, key=_representative_key):
        if is_low_resolution(candidate):
            continue
        if any(_same_scene(candidate, representative) for representative in representatives):
            continue
        retained.add(id(candidate))
        if _available(candidate):
            representatives.append(candidate)
    return [candidate for candidate in values if id(candidate) in retained]
