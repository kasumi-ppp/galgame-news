from __future__ import annotations

import hashlib
from collections.abc import Iterable

from ..config import PrescanConfig, load_config
from ..domain import CurationResult, FailureRecord, FailureStage, ImageCandidate, Issue, ReviewReason
from .allocator import ImageAllocator
from .dedup import Deduplicator
from .ranker import ImageRanker


class ImageCurator:
    """Coordinate validation-independent deduplication, ranking and allocation."""

    def __init__(self, config: PrescanConfig | None = None):
        self.config = config or load_config()

    def curate(self, issue: Issue, candidates: Iterable[ImageCandidate], history=None) -> CurationResult:
        values = list(candidates)
        failures: list[FailureRecord] = []
        historical_hashes = {}
        if history is not None and hasattr(history, "known_image"):
            for candidate in values:
                try:
                    if history.known_image(candidate.sha256, candidate.perceptual_hash) is not None:
                        if ReviewReason.HISTORICAL_DUPLICATE not in candidate.review_reasons:
                            candidate.review_reasons.append(ReviewReason.HISTORICAL_DUPLICATE)
                except Exception as exc:
                    failures.append(FailureRecord(stage=FailureStage.HISTORY, news_id=candidate.news_id, candidate_id=candidate.id, code="history_lookup_error", message=str(exc), retryable=True))
        deduped = Deduplicator().deduplicate(values, historical_hashes=historical_hashes)
        by_news = {item.id: item for item in issue.news_items}
        ranked: list[ImageCandidate] = []
        for news_id in sorted({candidate.news_id for candidate in deduped.unique}):
            item = by_news.get(news_id)
            group = [candidate for candidate in deduped.unique if candidate.news_id == news_id]
            if item is not None:
                ranked.extend(ImageRanker(self.config.scoring).rank(item, group))
            else:
                ranked.extend(group)
        allocated = ImageAllocator(min_images=self.config.selection.min_images, max_images=None, per_news_max=self.config.selection.per_news_max, minimum_score=self.config.selection.minimum_score).allocate(issue.news_items, ranked)
        return CurationResult(candidates=list(allocated), failures=failures, selection_shortfall=allocated.selection_shortfall)
