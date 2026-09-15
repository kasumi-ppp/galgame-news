from __future__ import annotations

from collections.abc import Iterable

from ..config import PrescanConfig, load_config
from ..domain import CurationResult, FailureRecord, FailureStage, ImageCandidate, ImageType, Issue, ReviewReason
from .allocator import ImageAllocator
from .dedup import Deduplicator
from .entity_matching import EntityMatcher
from .image_typing import ImageRequirementPolicy, ImageTypeClassifier, ImageTypeDecision, ImageTypeResult
from .ranker import ImageRanker
from .source_policy import SourceTrustPolicy


class ImageCurator:
    """Coordinate image typing, policy filtering, deduplication, ranking and allocation."""

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
        by_news = {item.id: item for item in issue.news_items}
        classifier = ImageTypeClassifier(self.config.image_types)
        policy = ImageRequirementPolicy(self.config.image_types)
        entity_matcher = EntityMatcher()
        source_policy = SourceTrustPolicy()
        eligible: list[ImageCandidate] = []
        filtered: list[ImageCandidate] = []
        for candidate in values:
            item = by_news.get(candidate.news_id)
            try:
                if item is None:
                    typed = ImageTypeResult(ImageType.UNKNOWN, 0.0, ("news_not_found",))
                    decision = ImageTypeDecision(True, requires_review=True, auto_select=False, type_match=0.1)
                else:
                    typed = classifier.classify(item, candidate)
                    decision = policy.evaluate(item, typed)
                candidate.image_type = typed.image_type
                candidate.image_type_confidence = typed.confidence
                source_result = source_policy.classify(candidate)
                candidate.signals["source_tier"] = source_result.tier
                candidate.signals["source_officiality"] = source_result.officiality
                candidate.signals["source_is_official"] = source_result.is_official
                if typed.supporting_signals:
                    candidate.signals["image_type_supporting_signals"] = ",".join(typed.supporting_signals)
                candidate.signals["type_match"] = decision.type_match
                candidate.signals["auto_select"] = decision.auto_select
                if decision.fallback_only:
                    candidate.signals["fallback_only"] = True
                if decision.requires_review and ReviewReason.IMAGE_TYPE_REVIEW not in candidate.review_reasons:
                    candidate.review_reasons.append(ReviewReason.IMAGE_TYPE_REVIEW)
                try:
                    match = entity_matcher.match(item, candidate) if item is not None else None
                except Exception as exc:
                    # A malformed page-local signal must not discard the
                    # other candidates for this news item.
                    candidate.signals["entity_match"] = "unknown"
                    candidate.signals["entity_match_confidence"] = 0.0
                    candidate.signals["auto_select"] = False
                    if ReviewReason.UNCERTAIN_MATCH not in candidate.review_reasons:
                        candidate.review_reasons.append(ReviewReason.UNCERTAIN_MATCH)
                    eligible.append(candidate)
                    failures.append(FailureRecord(stage=FailureStage.CURATE, news_id=candidate.news_id, candidate_id=candidate.id, code="entity_match_error", message=str(exc), source_url=candidate.image_url, retryable=False))
                    continue
                if match is not None:
                    candidate.signals["entity_match"] = match.matched if match.matched is not None else "unknown"
                    candidate.signals["entity_match_confidence"] = match.confidence
                    candidate.signals["entity_match_evidence"] = ",".join(match.supporting_signals)
                    candidate.signals["official_domain_match"] = match.official_domain_match
                    if match.conflicting_entities:
                        candidate.signals["entity_conflict"] = ",".join(match.conflicting_entities)
                    if match.matched is False:
                        candidate.selected = False
                        filtered.append(candidate)
                        failures.append(FailureRecord(stage=FailureStage.CURATE, news_id=candidate.news_id, candidate_id=candidate.id, code="entity_mismatch", message="candidate conflicts with the news game entity", source_url=candidate.image_url, retryable=False))
                        continue
                    if match.matched is None:
                        candidate.signals["auto_select"] = False
                        if ReviewReason.UNCERTAIN_MATCH not in candidate.review_reasons:
                            candidate.review_reasons.append(ReviewReason.UNCERTAIN_MATCH)
                        failures.append(FailureRecord(stage=FailureStage.CURATE, news_id=candidate.news_id, candidate_id=candidate.id, code="entity_unverified", message="candidate lacks sufficient game/entity context", source_url=candidate.image_url, retryable=False))
                    elif not source_result.is_official and match.confidence < 0.8:
                        candidate.signals["auto_select"] = False
                        if ReviewReason.UNCERTAIN_MATCH not in candidate.review_reasons:
                            candidate.review_reasons.append(ReviewReason.UNCERTAIN_MATCH)
                        failures.append(FailureRecord(stage=FailureStage.CURATE, news_id=candidate.news_id, candidate_id=candidate.id, code="entity_unverified", message="non-official candidate requires stronger entity evidence", source_url=candidate.image_url, retryable=False))
                if decision.accepted:
                    eligible.append(candidate)
                else:
                    candidate.selected = False
                    filtered.append(candidate)
                    failures.append(FailureRecord(
                        stage=FailureStage.CURATE,
                        news_id=candidate.news_id,
                        candidate_id=candidate.id,
                        code="image_type_rejected",
                        message=f"image_type={typed.image_type.value}; reason={decision.rejection_reason or 'rejected by image requirement policy'}",
                        source_url=candidate.image_url,
                        retryable=False,
                    ))
            except Exception as exc:
                candidate.image_type = ImageType.UNKNOWN
                candidate.image_type_confidence = 0.0
                candidate.signals["type_match"] = 0.1
                candidate.signals["auto_select"] = False
                if ReviewReason.IMAGE_TYPE_REVIEW not in candidate.review_reasons:
                    candidate.review_reasons.append(ReviewReason.IMAGE_TYPE_REVIEW)
                eligible.append(candidate)
                failures.append(FailureRecord(
                    stage=FailureStage.CURATE,
                    news_id=candidate.news_id,
                    candidate_id=candidate.id,
                    code="image_type_error",
                    message=str(exc),
                    source_url=candidate.image_url,
                    retryable=False,
                ))
        deduped = Deduplicator().deduplicate(eligible, historical_hashes=historical_hashes)
        ranked: list[ImageCandidate] = []
        for news_id in sorted({candidate.news_id for candidate in deduped.unique}):
            item = by_news.get(news_id)
            group = [candidate for candidate in deduped.unique if candidate.news_id == news_id]
            if item is not None:
                ranked.extend(ImageRanker(self.config.scoring, self.config.image_types).rank(item, group))
            else:
                ranked.extend(group)
        allocated = ImageAllocator(
            min_images=self.config.selection.min_images,
            max_images=None,
            per_news_max=self.config.selection.per_news_max,
            minimum_score=self.config.selection.minimum_score,
            max_unknown_per_news=self.config.image_types.max_unknown_per_news,
            type_limits=self.config.selection.type_limits,
        ).allocate(issue.news_items, ranked)
        return CurationResult(candidates=list(allocated), filtered_candidates=filtered, failures=failures, selection_shortfall=allocated.selection_shortfall)
