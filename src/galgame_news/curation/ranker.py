from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from ..config import ImageTypeConfig, ScoringConfig
from ..domain import ImageCandidate, ImageNeed, ImageType, NewsItem, ReviewReason, ScoreBreakdown, SourceType
from .image_typing import ImageRequirementPolicy


class ImageRanker:
    def __init__(self, scoring: ScoringConfig, image_types: ImageTypeConfig | None = None):
        self.scoring = scoring
        self.image_types = image_types or ImageTypeConfig()
        self.requirement_policy = ImageRequirementPolicy(self.image_types)

    def _relevance(self, candidate: ImageCandidate, news: NewsItem) -> float:
        signals = candidate.signals
        values = [float(signals[key]) for key in ("game_match", "organization_match", "page_match", "event_match", "character_match", "cg_match") if key in signals and isinstance(signals[key], (int, float))]
        if values:
            return max(0.0, min(1.0, sum(values) / len(values)))
        if news.image_need is ImageNeed.EXPLICIT_NEW_IMAGE and candidate.source_type is SourceType.OFFICIAL_SITE:
            return 0.65
        return 1.0 if candidate.source_type in {SourceType.OFFICIAL_SITE, SourceType.OFFICIAL_X} else 0.25

    def rank(self, news: NewsItem, candidates: Iterable[ImageCandidate], *, now: datetime | None = None) -> list[ImageCandidate]:
        values = list(candidates)
        reference_time = now or max((candidate.fetched_at for candidate in values), default=datetime.now(timezone.utc))
        ranked: list[ImageCandidate] = []
        for candidate in values:
            base_relevance = self._relevance(candidate, news)
            raw_type_match = candidate.signals.get("type_match")
            has_public_type = candidate.image_type is not ImageType.UNKNOWN
            if isinstance(raw_type_match, (int, float)):
                type_match = float(raw_type_match)
            elif has_public_type:
                type_match = self.requirement_policy.type_match(news, candidate.image_type)
            else:
                type_match = 0.5
            type_match = max(0.0, min(1.0, type_match))
            if candidate.signals.get("fallback_only") is True or (has_public_type and self.requirement_policy.is_fallback_only(news, candidate.image_type)):
                type_match = max(0.0, type_match - self.image_types.fallback_penalty)
            if isinstance(raw_type_match, (int, float)) or has_public_type:
                relevance = max(0.0, min(1.0, 0.55 * base_relevance + 0.45 * type_match))
            else:
                relevance = base_relevance
            if candidate.published_at is None:
                freshness = 0.0
                if ReviewReason.UNKNOWN_PUBLISH_TIME not in candidate.review_reasons: candidate.review_reasons.append(ReviewReason.UNKNOWN_PUBLISH_TIME)
            else:
                age_days = max(0.0, (reference_time - candidate.published_at).total_seconds() / 86400)
                freshness = max(0.0, min(1.0, 1.0 - age_days / 365.0))
            default_trust = {SourceType.OFFICIAL_SITE: 1.0, SourceType.OFFICIAL_X: 0.9, SourceType.STEAM: 0.85, SourceType.DIRECT_IMAGE: 0.65, SourceType.UNVERIFIED: 0.25}.get(candidate.source_type, 0.0)
            raw_trust = candidate.signals.get("source_officiality", default_trust)
            trust = max(0.0, min(1.0, float(raw_trust))) if isinstance(raw_trust, (int, float)) else default_trust
            width, height = candidate.width or 0, candidate.height or 0
            if width and height:
                # Treat 1280x720 as the preferred baseline. This avoids
                # ranking a very wide, short banner above a true 720p CG.
                quality = max(0.0, min(1.0, min(width / 1280.0, height / 720.0)))
            else:
                quality = 0.5
            duplicate_penalty = 1.0 if ReviewReason.HISTORICAL_DUPLICATE in candidate.review_reasons else 0.0
            risk_penalty = 1.0 if ReviewReason.ADULT_OR_UNKNOWN in candidate.review_reasons else 0.0
            total = 100 * (self.scoring.relevance * relevance + self.scoring.freshness * freshness + self.scoring.source_trust * trust + self.scoring.quality * quality - self.scoring.duplicate_penalty * duplicate_penalty - self.scoring.risk_penalty * risk_penalty)
            candidate.score = ScoreBreakdown(relevance=relevance, freshness=freshness, source_trust=trust, quality=quality, duplicate_penalty=duplicate_penalty, risk_penalty=risk_penalty, type_match=type_match, total=max(0.0, min(100.0, total)))
            ranked.append(candidate)
        ranked.sort(key=lambda c: (-(c.score.total if c.score else 0), -(c.score.source_trust if c.score else 0), -(c.published_at.timestamp() if c.published_at else 0), -((c.width or 0) * (c.height or 0)), c.id or ""))
        return ranked
