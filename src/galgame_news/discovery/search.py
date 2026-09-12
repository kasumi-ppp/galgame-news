"""Optional search providers; network clients are injectable for tests."""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any, Callable
from urllib.parse import urlsplit

from ..domain import DiscoveryMethod, NewsItem, SearchResult, SourceRef, SourceType


def _to_sources(results: Iterable[Any], method: DiscoveryMethod) -> list[SourceRef]:
    sources = []
    for result in results:
        if isinstance(result, SearchResult):
            value = result
        elif isinstance(result, dict):
            value = SearchResult(**result)
        else:
            value = SearchResult(url=str(getattr(result, "url", result)), title=str(getattr(result, "title", "")), snippet=str(getattr(result, "snippet", "")))
        host = urlsplit(value.url).hostname or "unknown"
        sources.append(SourceRef(url=value.url, domain=host, source_type=SourceType.UNVERIFIED, discovered_via=method, officiality=0.0))
    return sources


class DDGSSearchProvider:
    def __init__(self, *, search_fn: Callable[..., Iterable[Any]] | None = None, max_results: int = 10):
        self.search_fn = search_fn
        self.max_results = max_results

    def search(self, news_item: NewsItem) -> list[SourceRef]:
        query = " ".join(news_item.game_names or [news_item.title])
        if self.search_fn is None:
            try:
                from ddgs import DDGS
                results = DDGS().text(query, max_results=self.max_results)
            except Exception:
                return []
        else:
            results = self.search_fn(query, max_results=self.max_results)
        return _to_sources(list(results)[: self.max_results], DiscoveryMethod.DDGS)


class BraveSearchProvider:
    def __init__(self, *, api_key: str | None = None, request_fn: Callable[..., Any] | None = None, max_results: int = 10):
        self.api_key = api_key or os.getenv("BRAVE_SEARCH_API_KEY")
        self.request_fn = request_fn
        self.max_results = max_results

    def search(self, news_item: NewsItem) -> list[SourceRef]:
        if not self.api_key and self.request_fn is None:
            return []
        query = " ".join(news_item.game_names or [news_item.title])
        if self.request_fn is not None:
            payload = self.request_fn(query, api_key=self.api_key, count=self.max_results)
        else:
            import httpx
            response = httpx.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": self.max_results}, headers={"X-Subscription-Token": self.api_key}, timeout=12)
            response.raise_for_status()
            payload = response.json()
        results = payload.get("web", {}).get("results", []) if isinstance(payload, dict) else []
        return _to_sources(results[: self.max_results], DiscoveryMethod.BRAVE)


class FallbackSearchProvider:
    def __init__(self, brave: BraveSearchProvider | None = None, ddgs: DDGSSearchProvider | None = None):
        self.brave = brave or BraveSearchProvider()
        self.ddgs = ddgs or DDGSSearchProvider()

    def search(self, news_item: NewsItem) -> list[SourceRef]:
        results = self.brave.search(news_item)
        return results or self.ddgs.search(news_item)
