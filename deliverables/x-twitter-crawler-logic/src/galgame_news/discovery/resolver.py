"""Deterministic source resolution pipeline."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from ..domain import DiscoveryMethod, NewsItem, ReviewReason, SourceRef, SourceType
from .search import FallbackSearchProvider
from .http import SafeHttpClient


def _normalize(url: str) -> str:
    p = urlsplit(url.strip())
    return urlunsplit((p.scheme.casefold(), p.netloc.casefold(), p.path or "/", p.query, ""))


class DefaultSourceResolver:
    def __init__(self, *, history_lookup: Callable[[NewsItem], Iterable[SourceRef]] | None = None, same_domain_lookup: Callable[[SourceRef, NewsItem], Iterable[SourceRef]] | None = None, same_domain_depth: int = 1, same_domain_transport: Callable[..., Any] | None = None, search_provider: Callable[[NewsItem], Iterable[SourceRef]] | None = None, search_max_results: int = 5, search_timeout: float = 5.0):
        self.history_lookup = history_lookup or (lambda news: [])
        self.same_domain_lookup = same_domain_lookup
        self.same_domain_depth = max(0, min(3, same_domain_depth))
        self.same_domain_client = SafeHttpClient(transport=same_domain_transport, timeout=min(5.0, search_timeout), max_retries=1)
        self.search_provider = search_provider or FallbackSearchProvider(max_results=search_max_results, timeout=search_timeout).search

    @staticmethod
    def _document_source(url: str) -> SourceRef:
        parts = urlsplit(url)
        host = (parts.hostname or "unknown").casefold()
        path = parts.path.casefold()
        image_suffixes = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")
        video_suffixes = (".mp4", ".webm", ".m3u8")
        if path.endswith(video_suffixes):
            source_type, officiality = SourceType.VIDEO, 0.5
        elif path.endswith(image_suffixes):
            source_type, officiality = SourceType.DIRECT_IMAGE, 0.8
        elif host in {"x.com", "twitter.com", "www.x.com", "www.twitter.com"}:
            source_type, officiality = SourceType.OFFICIAL_X, 0.9
        elif host in {"store.steampowered.com", "steamcommunity.com"}:
            source_type, officiality = SourceType.STEAM, 0.85
        elif host in {"youtube.com", "www.youtube.com", "youtu.be", "bilibili.com", "www.bilibili.com"}:
            source_type, officiality = SourceType.VIDEO, 0.5
        elif host == "vndb.org" or host.endswith(".vndb.org"):
            source_type, officiality = SourceType.UNVERIFIED, 0.25
        else:
            source_type, officiality = SourceType.OFFICIAL_SITE, 0.75
        review = source_type in {SourceType.UNVERIFIED, SourceType.VIDEO}
        return SourceRef(
            url=url,
            domain=parts.hostname or "unknown",
            source_type=source_type,
            discovered_via=DiscoveryMethod.DOCUMENT,
            officiality=officiality,
            requires_review=review,
            review_reasons=[ReviewReason.UNCERTAIN_MATCH] if review else [],
        )

    @staticmethod
    def _gallery_link(base_url: str, href: str, label: str) -> str | None:
        target = _normalize(urljoin(base_url, href))
        base_host = (urlsplit(base_url).hostname or "").casefold()
        if (urlsplit(target).hostname or "").casefold() != base_host:
            return None
        evidence = f"{urlsplit(target).path} {label}".casefold()
        tokens = ("gallery", "/cg", "cg/", "special", "image", "visual", "news")
        return target if any(token in evidence for token in tokens) else None

    def _crawl_same_domain(self, source: SourceRef, news: NewsItem) -> list[SourceRef]:
        if self.same_domain_depth <= 0 or source.source_type is not SourceType.OFFICIAL_SITE:
            return []
        found: list[SourceRef] = []
        queue = deque([(source.url, 0)])
        visited = {_normalize(source.url)}
        while queue:
            page_url, depth = queue.popleft()
            if depth >= self.same_domain_depth:
                continue
            try:
                response = self.same_domain_client.get(page_url)
                effective_page_url = str(getattr(response, "url", page_url) or page_url)
                soup = BeautifulSoup(getattr(response, "text", "") or "", "html.parser")
            except Exception:
                continue
            links = [(anchor["href"], anchor.get_text(" ", strip=True)) for anchor in soup.find_all("a", href=True)]
            links.extend(
                (tag["data-izimodal-iframeurl"], "gallery modal")
                for tag in soup.find_all(attrs={"data-izimodal-iframeurl": True})
            )
            links.extend((tag["src"], "gallery iframe") for tag in soup.find_all("iframe", src=True))
            for tag in soup.find_all(True):
                for attr in ("data-iframe", "data-iframe-src", "data-src"):
                    value = tag.get(attr)
                    if value and not urlsplit(value).path.casefold().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")):
                        links.append((value, "gallery data link"))
            for href, label in links:
                child = self._gallery_link(effective_page_url, href, label)
                if not child or child in visited:
                    continue
                visited.add(child)
                found.append(SourceRef(url=child, domain=urlsplit(child).hostname or source.domain, source_type=SourceType.OFFICIAL_SITE, discovered_via=DiscoveryMethod.SAME_DOMAIN, officiality=source.officiality))
                queue.append((child, depth + 1))
        return found

    def resolve(self, news_item: NewsItem) -> list[SourceRef]:
        ordered: list[SourceRef] = []
        seen: set[str] = set()

        def add(source: SourceRef) -> None:
            key = _normalize(source.url)
            if key in seen: return
            seen.add(key)
            source.url = key
            if not source.domain: source.domain = urlsplit(key).hostname or "unknown"
            ordered.append(source)

        for url in news_item.source_urls:
            parts = urlsplit(url)
            if parts.scheme in {"http", "https"}:
                add(self._document_source(url))
        historical = list(self.history_lookup(news_item))
        for source in historical: add(source.model_copy(update={"discovered_via": DiscoveryMethod.HISTORY}))
        for source in list(ordered):
            children = self.same_domain_lookup(source, news_item) if self.same_domain_lookup else self._crawl_same_domain(source, news_item)
            for child in children: add(child.model_copy(update={"discovered_via": DiscoveryMethod.SAME_DOMAIN}))
        for source in self.search_provider(news_item):
            add(source)
            # A trusted official search hit is an entry point, so perform the
            # same bounded gallery discovery as document/history sources.
            if source.source_type is SourceType.OFFICIAL_SITE and source.officiality >= 0.75:
                for child in self._crawl_same_domain(source, news_item):
                    add(child.model_copy(update={"discovered_via": DiscoveryMethod.SAME_DOMAIN}))
        return ordered
