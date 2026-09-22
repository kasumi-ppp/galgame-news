"""HTML-backed source adapters."""

from __future__ import annotations

import json
import re
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ...domain import CollectionContext, CollectionResult, FailureRecord, FailureStage, ReviewReason
from ...video.discovery import discover_html_video_urls
from ..http import SafeHttpClient
from .common import (
    _candidate,
    _image_priority,
    _looks_like_image_url,
    _srcset_largest,
    _upgrade_image_url,
    _video_candidates,
)


class OfficialHtmlAdapter:
    def __init__(self, *, transport: Callable[..., Any] | None = None, client: SafeHttpClient | None = None):
        self.client = client or SafeHttpClient(transport=transport)

    def collect(self, news_item, source_ref, context: CollectionContext) -> CollectionResult:
        try:
            response = self.client.get(source_ref.url)
            page_url = str(getattr(response, "url", source_ref.url) or source_ref.url)
            html = getattr(response, "text", "") or ""
            soup = BeautifulSoup(html, "html.parser")
            lowered = html.casefold()
            if soup.find("meta", attrs={"name": lambda value: value and any(token in value.casefold().replace("-", " ").split() for token in ("age", "adult", "verification"))}) or "age-verification" in lowered or "age gate" in lowered:
                source_ref.requires_review = True
                source_ref.review_reasons = list(dict.fromkeys([*source_ref.review_reasons, ReviewReason.AGE_GATE]))
                return CollectionResult(manual_review_reasons=[ReviewReason.AGE_GATE])
            urls: list[str] = []
            thumbnail_values: set[str] = set()
            page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
            if not page_title:
                for meta in soup.find_all("meta"):
                    key = (meta.get("property") or meta.get("name") or "").casefold()
                    if key in {"og:title", "twitter:title"} and meta.get("content"):
                        page_title = str(meta["content"]).strip()
                        break
            image_context: dict[str, list[str]] = {}
            video_urls = discover_html_video_urls(html, page_url)

            def remember_context(raw_url: str, tag) -> None:
                if not isinstance(raw_url, str):
                    return
                normalized = _upgrade_image_url(urljoin(page_url, raw_url))
                alt = str(tag.get("alt") or "").strip()
                if normalized and alt:
                    image_context.setdefault(normalized, []).append(alt)

            for anchor in soup.find_all("a", href=True):
                href = anchor["href"]
                if _looks_like_image_url(href) or (anchor.find("img") is not None and any(token in urlsplit(href).path.casefold() for token in ("gallery", "cg", "image", "media"))):
                    urls.append(href)
                    for image in anchor.find_all("img"):
                        for attr in ("src", "data-src", "data-lazy-src", "data-original"):
                            if image.get(attr):
                                thumbnail_values.add(image[attr])
                                remember_context(image[attr], image)
            for tag in soup.find_all("meta"):
                key = (tag.get("property") or tag.get("name") or "").casefold()
                if key in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"} and tag.get("content"):
                    urls.append(tag["content"])
            for tag in soup.find_all("img"):
                for attr in ("data-original", "data-full", "data-large", "data-hires", "data-zoom-image", "src", "data-src", "data-lazy-src"):
                    if tag.get(attr):
                        remember_context(tag[attr], tag)
                        if tag[attr] not in thumbnail_values:
                            urls.append(tag[attr])
                if tag.get("srcset"):
                    largest = _srcset_largest(tag["srcset"])
                    if largest:
                        urls.append(largest)
            for tag in soup.find_all("source"):
                if tag.get("srcset"):
                    largest = _srcset_largest(tag["srcset"])
                    if largest:
                        urls.append(largest)
            for tag in soup.find_all(style=True):
                urls.extend(re.findall(r"url\(\s*['\"]?([^'\")]+)", tag.get("style", ""), flags=re.I))
            for tag in soup.find_all(True):
                for attr in ("data-background", "data-bg", "data-image"):
                    if tag.get(attr):
                        urls.append(tag[attr])
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string or script.get_text())
                    values = data.get("image", []) if isinstance(data, dict) else []
                    urls.extend(values if isinstance(values, list) else [values])
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue

            def collect_json_images(value: Any) -> None:
                if isinstance(value, str):
                    if value.startswith(("http://", "https://", "/", "wix:image://v1/")):
                        urls.append(value)
                elif isinstance(value, dict):
                    for child in value.values():
                        collect_json_images(child)
                elif isinstance(value, list):
                    for child in value:
                        collect_json_images(child)

            for script in soup.find_all("script"):
                text = script.string or script.get_text()
                if script.get("type") == "application/json" or script.get("id") == "__NEXT_DATA__" or "wix-warmup" in (script.get("id") or "").casefold() or "wix-viewer" in (script.get("id") or "").casefold():
                    try:
                        collect_json_images(json.loads(text))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        pass
            embedded = html.replace(r"\/", "/")
            urls.extend(re.findall(r"https://static\.wixstatic\.com/media/[^\"'<>\\\s]+", embedded))
            urls.extend(re.findall(r"wix:image://v1/[^\"'<>\\\s]+", embedded))
            candidates = []
            seen = set()
            for value in urls:
                if not isinstance(value, str):
                    continue
                image_url = _upgrade_image_url(urljoin(page_url, value))
                if not image_url or image_url in seen or urlsplit(image_url).scheme not in {"http", "https"}:
                    continue
                if not _looks_like_image_url(image_url):
                    continue
                seen.add(image_url)
                candidate = _candidate(news_item, image_url, source_ref, context)
                if page_title:
                    candidate.signals["page_title"] = page_title
                if image_context.get(image_url):
                    candidate.signals["alt"] = " ".join(dict.fromkeys(image_context[image_url]))
                if _image_priority(image_url) <= 1 or "/gallery" in urlsplit(source_ref.url).path.casefold():
                    candidate.signals["cg_match"] = 1.0
                candidates.append(candidate)
            if not candidates and not video_urls and soup.find("script"):
                source_ref.requires_review = True
                source_ref.review_reasons = list(dict.fromkeys([*source_ref.review_reasons, ReviewReason.DYNAMIC_PAGE]))
                return CollectionResult(manual_review_reasons=[ReviewReason.DYNAMIC_PAGE])
            candidates.sort(key=lambda candidate: _image_priority(candidate.image_url))
            return CollectionResult(candidates=candidates[:context.max_candidates], video_candidates=_video_candidates(news_item, source_ref, video_urls, page_title))
        except Exception as exc:
            return CollectionResult(failures=[FailureRecord(stage=FailureStage.COLLECT, news_id=news_item.id, code="adapter_error", message=str(exc), source_url=source_ref.url, retryable=True)])


class SteamAdapter(OfficialHtmlAdapter):
    """Steam pages use the same HTML metadata plus screenshot links."""


class DynamicPageAdapter(OfficialHtmlAdapter):
    """Metadata-only adapter for JavaScript pages; always requests review."""

    def collect(self, news_item, source_ref, context: CollectionContext) -> CollectionResult:
        result = super().collect(news_item, source_ref, context)
        source_ref.requires_review = True
        source_ref.review_reasons = list(dict.fromkeys([*source_ref.review_reasons, ReviewReason.DYNAMIC_PAGE]))
        result.manual_review_reasons = list(dict.fromkeys([*result.manual_review_reasons, ReviewReason.DYNAMIC_PAGE]))
        return result
