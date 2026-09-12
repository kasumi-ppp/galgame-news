from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from ..domain import ImageCandidate, NewsItem


class CandidateSelection(list[ImageCandidate]):
    def __init__(self, values=(), selection_shortfall: int = 0):
        super().__init__(values)
        self.selection_shortfall = selection_shortfall


class ImageAllocator:
    def __init__(self, *, min_images: int = 5, max_images: int = 20, per_news_max: int = 3, minimum_score: float = 0.0):
        self.min_images, self.max_images, self.per_news_max, self.minimum_score = min_images, max_images, per_news_max, minimum_score

    def allocate(self, news_items: Iterable[NewsItem], candidates: Iterable[ImageCandidate]) -> CandidateSelection:
        items = list(news_items)
        all_candidates = list(candidates)
        groups: dict[str, list[ImageCandidate]] = defaultdict(list)
        for candidate in all_candidates:
            if candidate.score is None or candidate.score.total >= self.minimum_score:
                groups[candidate.news_id].append(candidate)
        for group in groups.values(): group.sort(key=lambda c: (-(c.score.total if c.score else 0), c.id or ""))
        selected: list[ImageCandidate] = []
        # First pass guarantees visual representation for highest-quality news.
        for item in sorted(items, key=lambda value: (-value.importance, value.id or "")):
            group = groups.get(item.id or "", [])
            if group:
                group[0].selected = True
                selected.append(group[0])
        # Second pass fills remaining slots while honoring per-news caps.
        for item in sorted(items, key=lambda value: (-value.importance, value.id or "")):
            for candidate in groups.get(item.id or "", [])[1:self.per_news_max]:
                if len(selected) >= self.max_images: break
                if not candidate.selected:
                    candidate.selected = True
                    selected.append(candidate)
        shortfall = max(0, self.min_images - len(selected))
        return CandidateSelection(all_candidates, selection_shortfall=shortfall)
