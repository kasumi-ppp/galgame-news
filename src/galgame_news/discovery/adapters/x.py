"""X/Twitter source adapter with injectable public/API transports."""

from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ...domain import CollectionResult, FailureRecord, FailureStage, ReviewReason
from ...video.discovery import collect_entity_videos, discover_html_video_urls, explicit_x_html_entity_urls
from ..http import SafeHttpClient
from .common import _candidate, _is_public_x_media, _upgrade_image_url


class XAdapter:
    def __init__(self, *, token: str | None = None, transport: Callable[..., Any] | None = None, public_transport: Callable[..., Any] | None = None, public_resolver: Callable[[str], Any] | None = None):
        self.token = token
        self.transport = transport
        self.public_transport = public_transport
        self.public_resolver = public_resolver
        self.public_client = SafeHttpClient(transport=public_transport, resolver=public_resolver)
        # API transport and public-page transport are injectable, but all page
        # requests still pass through SafeHttpClient's URL/redirect checks.
        self.entity_client = SafeHttpClient(transport=public_transport or transport, resolver=public_resolver)

    def collect(self, news_item, source_ref, context) -> CollectionResult:
        source_ref.requires_review = True
        source_ref.review_reasons = list(dict.fromkeys([*source_ref.review_reasons, ReviewReason.X_SOURCE]))
        if not self.token:
            try:
                response = self.public_client.get(source_ref.url)
                html = getattr(response, "text", "") or ""
                soup = BeautifulSoup(html, "html.parser")
                urls = []
                for tag in soup.find_all("meta"):
                    key = (tag.get("property") or tag.get("name") or "").casefold()
                    if key in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"} and tag.get("content"):
                        image_url = _upgrade_image_url(urljoin(source_ref.url, tag["content"]))
                        if not image_url:
                            continue
                        image_host = (urlsplit(image_url).hostname or "").casefold()
                        image_path = urlsplit(image_url).path.casefold()
                        if image_host == "pbs.twimg.com" and (image_path.startswith("/media/") or image_path.startswith("/card_img/")):
                            urls.append(image_url)

                # Only tags/embeds are trusted directly from the X page.  An
                # anchor is accepted only with explicit tweet/card context.
                video_urls = discover_html_video_urls(
                    html,
                    source_ref.url,
                    include_anchor_links=False,
                )
                links = [
                    {"expanded_url": url}
                    for url in explicit_x_html_entity_urls(html, source_ref.url)
                ]
                links.extend({"expanded_url": url} for url in video_urls)
                entity_failures: list[FailureRecord] = []

                def fetch_html(url: str) -> tuple[str, str]:
                    child = self.public_client.get(url)
                    final_url = str(getattr(child, "url", url) or url)
                    return final_url, getattr(child, "text", "") or ""

                entity_videos = collect_entity_videos(
                    news_item,
                    source_ref,
                    {"entities": {"urls": links}},
                    fetch_html=fetch_html,
                    url_validator=self.public_client.validate_url,
                    failures=entity_failures,
                )
                combined = {candidate.video_url: candidate for candidate in entity_videos}
                return CollectionResult(
                    candidates=[_candidate(news_item, url, source_ref, context) for url in dict.fromkeys(urls)],
                    video_candidates=list(combined.values()),
                    failures=entity_failures,
                    manual_review_reasons=[ReviewReason.X_SOURCE],
                )
            except Exception as exc:
                return CollectionResult(failures=[FailureRecord(stage=FailureStage.COLLECT, news_id=news_item.id, code="x_public_metadata_error", message=str(exc), source_url=source_ref.url, retryable=True)], manual_review_reasons=[ReviewReason.X_SOURCE, ReviewReason.NETWORK_RESTRICTED])

        # API integration is deliberately injectable; no login or scraping fallback.
        if self.transport is None:
            return CollectionResult(manual_review_reasons=[ReviewReason.X_SOURCE])
        try:
            response = self.transport(source_ref.url, token=self.token, timeout=context.timeout_seconds)
            data = response if isinstance(response, dict) else getattr(response, "json", lambda: {})()
            urls = data.get("image_urls", []) if isinstance(data, dict) else []
            entity_failures: list[FailureRecord] = []

            def fetch_html(url: str) -> tuple[str, str]:
                child = self.entity_client.get(url)
                final_url = str(getattr(child, "url", url) or url)
                return final_url, getattr(child, "text", "") or ""

            entity_videos = collect_entity_videos(
                news_item,
                source_ref,
                data,
                fetch_html=fetch_html,
                url_validator=self.entity_client.validate_url,
                failures=entity_failures,
            )
            return CollectionResult(
                candidates=[_candidate(news_item, url, source_ref, context) for url in urls if isinstance(url, str) and _is_public_x_media(url)],
                video_candidates=entity_videos,
                failures=entity_failures,
                manual_review_reasons=[ReviewReason.X_SOURCE],
            )
        except Exception as exc:
            return CollectionResult(failures=[FailureRecord(stage=FailureStage.COLLECT, news_id=news_item.id, code="x_api_error", message=str(exc), source_url=source_ref.url, retryable=True)], manual_review_reasons=[ReviewReason.X_SOURCE])
