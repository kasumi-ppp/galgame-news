"""Source adapters. Adapters never bypass login or age restrictions."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ..domain import CollectionContext, CollectionResult, FailureRecord, FailureStage, ImageCandidate, NewsItem, ReviewReason, SourceRef, SourceType
from .http import SafeHttpClient


def _candidate(news: NewsItem, image_url: str, source: SourceRef, context: CollectionContext) -> ImageCandidate:
    return ImageCandidate(news_id=news.id or "", image_url=image_url, source_url=source.url, source_type=source.source_type, fetched_at=context.now, downloadable=True)


class DirectImageAdapter:
    def collect(self, news_item: NewsItem, source_ref: SourceRef, context: CollectionContext) -> CollectionResult:
        return CollectionResult(candidates=[_candidate(news_item, source_ref.url, source_ref, context)])


class OfficialHtmlAdapter:
    def __init__(self, *, transport: Callable[..., Any] | None = None, client: SafeHttpClient | None = None):
        self.client = client or SafeHttpClient(transport=transport)

    def collect(self, news_item: NewsItem, source_ref: SourceRef, context: CollectionContext) -> CollectionResult:
        try:
            response = self.client.get(source_ref.url)
            html = getattr(response, "text", "") or ""
            soup = BeautifulSoup(html, "html.parser")
            lowered = html.casefold()
            if soup.find("meta", attrs={"name": lambda value: value and any(token in value.casefold().replace("-", " ").split() for token in ("age", "adult", "verification"))}) or "age-verification" in lowered or "age gate" in lowered:
                source_ref.requires_review = True
                source_ref.review_reasons = list(dict.fromkeys([*source_ref.review_reasons, ReviewReason.AGE_GATE]))
                return CollectionResult(manual_review_reasons=[ReviewReason.AGE_GATE])
            urls: list[str] = []
            for tag in soup.find_all("meta"):
                key = (tag.get("property") or tag.get("name") or "").casefold()
                if key in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"} and tag.get("content"):
                    urls.append(tag["content"])
            for tag in soup.find_all("img"):
                for attr in ("src", "data-src", "data-lazy-src", "data-original"):
                    if tag.get(attr): urls.append(tag[attr])
                if tag.get("srcset"):
                    urls.append(tag["srcset"].split(",")[-1].strip().split()[0])
            for tag in soup.find_all("a", href=True):
                href = tag["href"]
                if urlsplit(href).path.casefold().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
                    urls.append(href)
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string or script.get_text())
                    values = data.get("image", []) if isinstance(data, dict) else []
                    urls.extend(values if isinstance(values, list) else [values])
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
            candidates = []
            seen = set()
            for value in urls:
                if not isinstance(value, str): continue
                image_url = urljoin(source_ref.url, value)
                if image_url in seen or urlsplit(image_url).scheme not in {"http", "https"}: continue
                seen.add(image_url)
                candidates.append(_candidate(news_item, image_url, source_ref, context))
            if not candidates and soup.find("script"):
                source_ref.requires_review = True
                source_ref.review_reasons = list(dict.fromkeys([*source_ref.review_reasons, ReviewReason.DYNAMIC_PAGE]))
                return CollectionResult(manual_review_reasons=[ReviewReason.DYNAMIC_PAGE])
            return CollectionResult(candidates=candidates[:context.max_candidates])
        except Exception as exc:
            return CollectionResult(failures=[FailureRecord(stage=FailureStage.COLLECT, news_id=news_item.id, code="adapter_error", message=str(exc), source_url=source_ref.url, retryable=True)])


class SteamAdapter(OfficialHtmlAdapter):
    """Steam pages use the same HTML metadata plus screenshot links."""


class DynamicPageAdapter(OfficialHtmlAdapter):
    """Metadata-only adapter for JavaScript pages; always requests review."""

    def collect(self, news_item: NewsItem, source_ref: SourceRef, context: CollectionContext) -> CollectionResult:
        result = super().collect(news_item, source_ref, context)
        source_ref.requires_review = True
        source_ref.review_reasons = list(dict.fromkeys([*source_ref.review_reasons, ReviewReason.DYNAMIC_PAGE]))
        result.manual_review_reasons = list(dict.fromkeys([*result.manual_review_reasons, ReviewReason.DYNAMIC_PAGE]))
        return result


class VideoAdapter:
    def collect(self, news_item: NewsItem, source_ref: SourceRef, context: CollectionContext) -> CollectionResult:
        source_ref.requires_review = True
        source_ref.review_reasons = list(dict.fromkeys([*source_ref.review_reasons, ReviewReason.DYNAMIC_PAGE]))
        return CollectionResult(manual_review_reasons=[ReviewReason.DYNAMIC_PAGE])


class XAdapter:
    def __init__(self, *, token: str | None = None, transport: Callable[..., Any] | None = None):
        self.token = token
        self.transport = transport

    def collect(self, news_item: NewsItem, source_ref: SourceRef, context: CollectionContext) -> CollectionResult:
        source_ref.requires_review = True
        source_ref.review_reasons = list(dict.fromkeys([*source_ref.review_reasons, ReviewReason.X_SOURCE]))
        if not self.token:
            return CollectionResult(manual_review_reasons=[ReviewReason.X_SOURCE])
        # API integration is deliberately injectable; no login or scraping fallback.
        if self.transport is None:
            return CollectionResult(manual_review_reasons=[ReviewReason.X_SOURCE])
        try:
            response = self.transport(source_ref.url, token=self.token, timeout=context.timeout_seconds)
            data = response if isinstance(response, dict) else getattr(response, "json", lambda: {})()
            urls = data.get("image_urls", []) if isinstance(data, dict) else []
            return CollectionResult(candidates=[_candidate(news_item, url, source_ref, context) for url in urls if isinstance(url, str)], manual_review_reasons=[ReviewReason.X_SOURCE])
        except Exception as exc:
            return CollectionResult(failures=[FailureRecord(stage=FailureStage.COLLECT, news_id=news_item.id, code="x_api_error", message=str(exc), source_url=source_ref.url, retryable=True)], manual_review_reasons=[ReviewReason.X_SOURCE])
