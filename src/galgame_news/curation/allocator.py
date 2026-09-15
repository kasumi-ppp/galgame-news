from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from ..domain import ImageCandidate, ImageType, NewsItem


class CandidateSelection(list[ImageCandidate]):
    def __init__(self, values=(), selection_shortfall: int = 0):
        super().__init__(values)
        self.selection_shortfall = selection_shortfall


class ImageAllocator:
    def __init__(self, *, min_images: int = 5, max_images: int | None = 20, per_news_max: int = 20, minimum_score: float = 0.0, max_unknown_per_news: int | None = None):
        self.min_images, self.max_images, self.per_news_max, self.minimum_score, self.max_unknown_per_news = min_images, max_images, per_news_max, minimum_score, max_unknown_per_news

    def allocate(self, news_items: Iterable[NewsItem], candidates: Iterable[ImageCandidate]) -> CandidateSelection:
        items = list(news_items)
        all_candidates = list(candidates)
        groups: dict[str, list[ImageCandidate]] = defaultdict(list)
        for candidate in all_candidates:
            if candidate.signals.get("auto_select", True) is False:
                continue
            if candidate.image_type is ImageType.UNKNOWN and self.max_unknown_per_news is not None and self.max_unknown_per_news <= 0:
                continue
            if candidate.score is None or candidate.score.total >= self.minimum_score:
                groups[candidate.news_id].append(candidate)
        for group in groups.values(): group.sort(key=lambda c: (-(c.score.total if c.score else 0), c.id or ""))
        for news_id, group in list(groups.items()):
            preferred = [candidate for candidate in group if candidate.signals.get("fallback_only") is not True]
            if preferred:
                groups[news_id] = preferred
        selected: list[ImageCandidate] = []
        unknown_selected: dict[str, int] = defaultdict(int)

        def can_select(candidate: ImageCandidate) -> bool:
            return not (candidate.image_type is ImageType.UNKNOWN and self.max_unknown_per_news is not None and unknown_selected[candidate.news_id] >= self.max_unknown_per_news)

        def select(candidate: ImageCandidate) -> None:
            candidate.selected = True
            selected.append(candidate)
            if candidate.image_type is ImageType.UNKNOWN:
                unknown_selected[candidate.news_id] += 1
        # First pass guarantees visual representation for highest-quality news.
        for item in sorted(items, key=lambda value: (-value.importance, value.id or "")):
            group = groups.get(item.id or "", [])
            if group:
                first = next((candidate for candidate in group if can_select(candidate)), None)
                if first is not None:
                    select(first)
        # Second pass fills remaining slots while honoring per-news caps.
        for item in sorted(items, key=lambda value: (-value.importance, value.id or "")):
            for candidate in groups.get(item.id or "", [])[1:self.per_news_max]:
                if self.max_images is not None and len(selected) >= self.max_images: break
                if not candidate.selected and can_select(candidate):
                    select(candidate)
        shortfall = sum(max(0, self.min_images - sum(candidate.selected for candidate in groups.get(item.id or "", []))) for item in items)
        return CandidateSelection(all_candidates, selection_shortfall=shortfall)
