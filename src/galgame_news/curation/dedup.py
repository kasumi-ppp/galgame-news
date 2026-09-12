from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..domain import ImageCandidate, ReviewReason


@dataclass
class DedupResult:
    unique: list[ImageCandidate]
    duplicates: list[ImageCandidate]


class Deduplicator:
    def deduplicate(self, candidates: Iterable[ImageCandidate], *, historical_hashes: dict[str, str] | None = None) -> DedupResult:
        seen_sha: set[str] = set()
        seen_phash: set[str] = set()
        unique: list[ImageCandidate] = []
        duplicates: list[ImageCandidate] = []
        historical_hashes = historical_hashes or {}
        for candidate in candidates:
            if candidate.signals.get("fallback_old_material") and ReviewReason.FALLBACK_OLD_MATERIAL not in candidate.review_reasons:
                candidate.review_reasons.append(ReviewReason.FALLBACK_OLD_MATERIAL)
            historical = candidate.sha256 and candidate.sha256 in historical_hashes or candidate.perceptual_hash and candidate.perceptual_hash in historical_hashes.values()
            if historical:
                if ReviewReason.HISTORICAL_DUPLICATE not in candidate.review_reasons:
                    candidate.review_reasons.append(ReviewReason.HISTORICAL_DUPLICATE)
            duplicate = bool(candidate.sha256 and candidate.sha256 in seen_sha) or bool(candidate.perceptual_hash and candidate.perceptual_hash in seen_phash)
            if duplicate:
                if ReviewReason.HISTORICAL_DUPLICATE not in candidate.review_reasons:
                    candidate.review_reasons.append(ReviewReason.HISTORICAL_DUPLICATE)
                duplicates.append(candidate)
                continue
            unique.append(candidate)
            if candidate.sha256: seen_sha.add(candidate.sha256)
            if candidate.perceptual_hash: seen_phash.add(candidate.perceptual_hash)
        return DedupResult(unique=unique, duplicates=duplicates)
