"""Source adapters. Adapters never bypass login or age restrictions."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from ..domain import CollectionContext, CollectionResult, FailureRecord, FailureStage, ImageCandidate, NewsItem, ReviewReason, SourceRef, SourceType
from .http import SafeHttpClient
from ..video.discovery import collect_entity_videos, discover_html_video_urls, explicit_x_html_entity_urls, video_candidate


def _candidate(news: NewsItem, image_url: str, source: SourceRef, context: CollectionContext) -> ImageCandidate:
    signals: dict[str, float | str | bool] = {"source_officiality": source.officiality}
    if source.source_type in {SourceType.OFFICIAL_X, SourceType.DIRECT_IMAGE}:
        signals["event_match"] = 1.0
    return ImageCandidate(
        news_id=news.id or "",
        image_url=image_url,
        source_url=source.url,
        source_type=source.source_type,
        fetched_at=context.now,
        downloadable=True,
        review_reasons=list(source.review_reasons),
        signals=signals,
    )


def _upgrade_wix(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.hostname and parsed.hostname.casefold().endswith(".wixsite.com"):
        if re.search(r"/(?:q_90|quality_auto)(?:/|$)", parsed.path.casefold()):
            return None
    if url.startswith("wix:image://v1/"):
        media_id = url.split("wix:image://v1/", 1)[1].split("/", 1)[0]
        return f"https://static.wixstatic.com/media/{media_id}"
    parts = urlsplit(url)
    if parts.hostname and parts.hostname.casefold() == "static.wixstatic.com" and "/v1/" in parts.path and "/media/" in parts.path:
        media_id = parts.path.split("/media/", 1)[1].split("/v1/", 1)[0]
        return f"https://static.wixstatic.com/media/{media_id}"
    return url


def _upgrade_image_url(url: str) -> str | None:
    """Remove common CDN thumbnail transforms while preserving the original asset."""
    upgraded = _upgrade_wix(url)
    if not upgraded:
        return None
    parts = urlsplit(upgraded)
    host = (parts.hostname or "").casefold()
    path = re.sub(r"(?:[-_]\d{2,5}x\d{2,5})(?=\.[a-z0-9]{2,5}$)", "", parts.path, flags=re.I)
    query = parse_qsl(parts.query, keep_blank_values=True)
    if host == "pbs.twimg.com":
        query = [(key, "orig" if key.casefold() == "name" else value) for key, value in query if key.casefold() not in {"width", "height"}]
    elif host in {"images.ctfassets.net", "i0.wp.com", "cdn.discordapp.com"}:
        query = [(key, value) for key, value in query if key.casefold() not in {"w", "h", "width", "height", "resize", "fit", "crop", "q", "quality"}]
    elif "cloudinary.com" in host:
        path = re.sub(r"/(?:f_[^,/]+,?|q_[^,/]+,?|w_\d+,?|h_\d+,?|c_[^,/]+,?)+(?=/)", "", path, flags=re.I)
    return urlunsplit((parts.scheme, parts.netloc, path, urlencode(query), parts.fragment))


def _srcset_largest(value: str) -> str | None:
    entries = []
    for raw in value.split(","):
        bits = raw.strip().split()
        if not bits:
            continue
        weight = 0
        if len(bits) > 1:
            match = re.match(r"(\d+)(?:w|x)$", bits[1])
            weight = int(match.group(1)) if match else 0
        entries.append((weight, bits[0]))
    return max(entries, key=lambda pair: pair[0])[1] if entries else None


def _looks_like_image_url(url: str) -> bool:
    path = urlsplit(url).path.casefold()
    if path.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")):
        return True
    return any(f"/{token}/" in f"{path}/" for token in ("gallery", "cg", "images", "media", "screenshot", "screenshots")) and not path.endswith("/")


def _image_priority(url: str) -> int:
    path = urlsplit(url).path.casefold()
    filename = path.rsplit("/", 1)[-1]
    if "/gallery/" in path or "/cg/" in path or re.match(r"(?:cg|gallery|ss[_-]?)\d+", filename):
        return 0
    if "/screenshot" in path or "/sample" in path or "/points/" in path:
        return 1
    return 2


def _is_public_x_media(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").casefold()
    path = parts.path.casefold()
    return host == "pbs.twimg.com" and (path.startswith("/media/") or path.startswith("/card_img/"))


class DirectImageAdapter:
    def collect(self, news_item: NewsItem, source_ref: SourceRef, context: CollectionContext) -> CollectionResult:
        image_url = _upgrade_image_url(source_ref.url) or source_ref.url
        return CollectionResult(candidates=[_candidate(news_item, image_url, source_ref, context)])


def _video_candidates(news_item: NewsItem, source_ref: SourceRef, urls: list[str], title: str = "") -> list:
    result = []
    seen = set()
    for url in urls:
        candidate = video_candidate(news_item, url, source_ref, title=title)
        if candidate is not None and candidate.video_url not in seen:
            seen.add(candidate.video_url)
            result.append(candidate)
    return result


class OfficialHtmlAdapter:
    def __init__(self, *, transport: Callable[..., Any] | None = None, client: SafeHttpClient | None = None):
        self.client = client or SafeHttpClient(transport=transport)

    def collect(self, news_item: NewsItem, source_ref: SourceRef, context: CollectionContext) -> CollectionResult:
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
                    if largest: urls.append(largest)
            for tag in soup.find_all("source"):
                if tag.get("srcset"):
                    largest = _srcset_largest(tag["srcset"])
                    if largest: urls.append(largest)
            for tag in soup.find_all(style=True):
                urls.extend(re.findall(r"url\(\s*['\"]?([^'\")]+)", tag.get("style", ""), flags=re.I))
            for tag in soup.find_all(True):
                for attr in ("data-background", "data-bg", "data-image"):
                    if tag.get(attr): urls.append(tag[attr])
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
                    for child in value.values(): collect_json_images(child)
                elif isinstance(value, list):
                    for child in value: collect_json_images(child)
            for script in soup.find_all("script"):
                text = script.string or script.get_text()
                if script.get("type") == "application/json" or script.get("id") == "__NEXT_DATA__" or "wix-warmup" in (script.get("id") or "").casefold() or "wix-viewer" in (script.get("id") or "").casefold():
                    try: collect_json_images(json.loads(text))
                    except (TypeError, ValueError, json.JSONDecodeError): pass
            embedded = html.replace(r"\/", "/")
            urls.extend(re.findall(r"https://static\.wixstatic\.com/media/[^\"'<>\\\s]+", embedded))
            urls.extend(re.findall(r"wix:image://v1/[^\"'<>\\\s]+", embedded))
            candidates = []
            seen = set()
            for value in urls:
                if not isinstance(value, str): continue
                image_url = _upgrade_image_url(urljoin(page_url, value))
                if not image_url or image_url in seen or urlsplit(image_url).scheme not in {"http", "https"}: continue
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

    def collect(self, news_item: NewsItem, source_ref: SourceRef, context: CollectionContext) -> CollectionResult:
        result = super().collect(news_item, source_ref, context)
        source_ref.requires_review = True
        source_ref.review_reasons = list(dict.fromkeys([*source_ref.review_reasons, ReviewReason.DYNAMIC_PAGE]))
        result.manual_review_reasons = list(dict.fromkeys([*result.manual_review_reasons, ReviewReason.DYNAMIC_PAGE]))
        return result


class VideoAdapter:
    def collect(self, news_item: NewsItem, source_ref: SourceRef, context: CollectionContext) -> CollectionResult:
        candidate = video_candidate(news_item, source_ref.url, source_ref)
        source_ref.requires_review = True
        source_ref.review_reasons = list(dict.fromkeys([*source_ref.review_reasons, ReviewReason.DYNAMIC_PAGE]))
        return CollectionResult(
            video_candidates=[candidate] if candidate is not None else [],
            manual_review_reasons=[ReviewReason.DYNAMIC_PAGE],
        )


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

    def collect(self, news_item: NewsItem, source_ref: SourceRef, context: CollectionContext) -> CollectionResult:
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