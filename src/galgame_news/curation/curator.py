from __future__ import annotations

import hashlib
from collections.abc import Iterable

from ..config import PrescanConfig, load_config
from ..domain import CurationResult, ImageCandidate, Issue
from .allocator import ImageAllocator
from .dedup import Deduplicator
from .ranker import ImageRanker


class ImageCurator:
    """Coordinate validation-independent deduplication, ranking and allocation."""

    def __init__(self, config: PrescanConfig | None = None):
        self.config = config or load_config()

    def curate(self, issue: Issue, candidates: Iterable[ImageCandidate], history=None) -> CurationResult:
        values = list(candidates)
        historical = {}
        if history is not None and hasattr(history, "known_image"):
            historical = getattr(history, "image_hashes", {}) or {}
        deduped = Deduplicator().deduplicate(values, historical_hashes=historical)
        by_news = {item.id: item for item in issue.news_items}
        ranked: list[ImageCandidate] = []
        for news_id in sorted({candidate.news_id for candidate in deduped.unique}):
            item = by_news.get(news_id)
            group = [candidate for candidate in deduped.unique if candidate.news_id == news_id]
            if item is not None:
                ranked.extend(ImageRanker(self.config.scoring).rank(item, group)[: self.config.selection.per_news_max])
            else:
                ranked.extend(group)
        allocated = ImageAllocator(min_images=self.config.selection.min_images, max_images=self.config.selection.max_images, per_news_max=self.config.selection.per_news_max, minimum_score=self.config.selection.minimum_score).allocate(issue.news_items, ranked)
        return CurationResult(candidates=list(allocated), selection_shortfall=allocated.selection_shortfall)
