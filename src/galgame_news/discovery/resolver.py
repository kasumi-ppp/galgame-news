"""Deterministic source resolution pipeline."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

from ..domain import DiscoveryMethod, NewsItem, SourceRef, SourceType
from .search import FallbackSearchProvider


def _normalize(url: str) -> str:
    p = urlsplit(url.strip())
    return urlunsplit((p.scheme.casefold(), p.netloc.casefold(), p.path or "/", p.query, ""))


class DefaultSourceResolver:
    def __init__(self, *, history_lookup: Callable[[NewsItem], Iterable[SourceRef]] | None = None, same_domain_lookup: Callable[[SourceRef, NewsItem], Iterable[SourceRef]] | None = None, search_provider: Callable[[NewsItem], Iterable[SourceRef]] | None = None):
        self.history_lookup = history_lookup or (lambda news: [])
        self.same_domain_lookup = same_domain_lookup or (lambda source, news: [])
        self.search_provider = search_provider or FallbackSearchProvider().search

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
                add(SourceRef(url=url, domain=parts.hostname or "unknown", source_type=SourceType.OFFICIAL_X if parts.hostname and parts.hostname.casefold() in {"x.com", "twitter.com"} else SourceType.OFFICIAL_SITE, discovered_via=DiscoveryMethod.DOCUMENT, officiality=1.0))
        historical = list(self.history_lookup(news_item))
        for source in historical: add(source.model_copy(update={"discovered_via": DiscoveryMethod.HISTORY}))
        for source in list(ordered):
            for child in self.same_domain_lookup(source, news_item): add(child.model_copy(update={"discovered_via": DiscoveryMethod.SAME_DOMAIN}))
        for source in self.search_provider(news_item): add(source)
        return ordered
