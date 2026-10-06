"""Shared normalization and candidate helpers for discovery adapters."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ...domain import CollectionContext, ImageCandidate, NewsItem, SourceRef, SourceType
from ...video.discovery import video_candidate


def _candidate(news: NewsItem, image_url: str, source: SourceRef, context: CollectionContext) -> ImageCandidate:
    signals: dict[str, float | str | bool] = {"source_officiality": source.officiality}
    if source.root_url:
        signals["root_source_url"] = source.root_url
    if source.parent_url:
        signals["parent_source_url"] = source.parent_url
    signals["source_discovery"] = source.discovered_via.value
    return ImageCandidate(
        news_id=news.id or "",
        image_url=image_url,
        source_url=source.url,
        source_type=source.source_type,
        fetched_at=context.now,
        downloadable=True,
        news_source_url=source.root_url or source.url,
        parent_source_url=source.parent_url or source.url,
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


def _video_candidates(news_item: NewsItem, source_ref: SourceRef, urls: list[str], title: str = "") -> list:
    result = []
    seen = set()
    for url in urls:
        candidate = video_candidate(news_item, url, source_ref, title=title)
        if candidate is not None and candidate.video_url not in seen:
            seen.add(candidate.video_url)
            result.append(candidate)
    return result
