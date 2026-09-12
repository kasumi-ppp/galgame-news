from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from ..config import ScoringConfig
from ..domain import ImageCandidate, NewsItem, ReviewReason, ScoreBreakdown, SourceType


class ImageRanker:
    def __init__(self, scoring: ScoringConfig):
        self.scoring = scoring

    def _relevance(self, candidate: ImageCandidate, news: NewsItem) -> float:
        signals = candidate.signals
        values = [float(signals[key]) for key in ("game_match", "organization_match", "page_match", "event_match", "character_match") if key in signals and isinstance(signals[key], (int, float))]
        return max(0.0, min(1.0, (sum(values) / len(values)) if values else (1.0 if candidate.source_type in {SourceType.OFFICIAL_SITE, SourceType.OFFICIAL_X} else 0.25)))

    def rank(self, news: NewsItem, candidates: Iterable[ImageCandidate]) -> list[ImageCandidate]:
        now = datetime.now(timezone.utc)
        ranked: list[ImageCandidate] = []
        for candidate in candidates:
            relevance = self._relevance(candidate, news)
            if candidate.published_at is None:
                freshness = 0.0
                if ReviewReason.UNKNOWN_PUBLISH_TIME not in candidate.review_reasons: candidate.review_reasons.append(ReviewReason.UNKNOWN_PUBLISH_TIME)
            else:
                age_days = max(0.0, (now - candidate.published_at).total_seconds() / 86400)
                freshness = max(0.0, min(1.0, 1.0 - age_days / 365.0))
            trust = {SourceType.OFFICIAL_SITE: 1.0, SourceType.OFFICIAL_X: 0.9, SourceType.STEAM: 0.85, SourceType.DIRECT_IMAGE: 0.65, SourceType.UNVERIFIED: 0.25}.get(candidate.source_type, 0.0)
            pixels = (candidate.width or 0) * (candidate.height or 0)
            quality = min(1.0, pixels / 1_000_000) if pixels else 0.5
            duplicate_penalty = 1.0 if ReviewReason.HISTORICAL_DUPLICATE in candidate.review_reasons else 0.0
            risk_penalty = 1.0 if ReviewReason.ADULT_OR_UNKNOWN in candidate.review_reasons else 0.0
            total = 100 * (self.scoring.relevance * relevance + self.scoring.freshness * freshness + self.scoring.source_trust * trust + self.scoring.quality * quality - self.scoring.duplicate_penalty * duplicate_penalty - self.scoring.risk_penalty * risk_penalty)
            candidate.score = ScoreBreakdown(relevance=relevance, freshness=freshness, source_trust=trust, quality=quality, duplicate_penalty=duplicate_penalty, risk_penalty=risk_penalty, total=max(0.0, min(100.0, total)))
            ranked.append(candidate)
        ranked.sort(key=lambda c: (-(c.score.total if c.score else 0), -(c.score.source_trust if c.score else 0), -(c.published_at.timestamp() if c.published_at else 0), -((c.width or 0) * (c.height or 0)), c.id or ""))
        return ranked
