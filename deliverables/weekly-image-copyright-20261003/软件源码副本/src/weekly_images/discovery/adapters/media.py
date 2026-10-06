"""Direct image and video source adapters."""

from __future__ import annotations

from ...domain import CollectionContext, CollectionResult, ReviewReason
from ...video.discovery import video_candidate
from .common import _candidate, _upgrade_image_url


class DirectImageAdapter:
    def collect(self, news_item, source_ref, context: CollectionContext) -> CollectionResult:
        image_url = _upgrade_image_url(source_ref.url) or source_ref.url
        return CollectionResult(candidates=[_candidate(news_item, image_url, source_ref, context)])


class VideoAdapter:
    def collect(self, news_item, source_ref, context: CollectionContext) -> CollectionResult:
        candidate = video_candidate(news_item, source_ref.url, source_ref)
        source_ref.requires_review = True
        source_ref.review_reasons = list(dict.fromkeys([*source_ref.review_reasons, ReviewReason.DYNAMIC_PAGE]))
        return CollectionResult(
            video_candidates=[candidate] if candidate is not None else [],
            manual_review_reasons=[ReviewReason.DYNAMIC_PAGE],
        )
