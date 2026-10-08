"""Dispatch localization-source collection without altering the caller's news item."""

from __future__ import annotations

from urllib.parse import urlsplit

from ..domain import CollectionContext, CollectionResult
from .steam import SteamLocalizationAdapter
from .vndb import VNDBLocalizationAdapter
from .vndb_client import VNDBClient


class LocalizationImageService:
    def __init__(self, http_client):
        self.http_client = http_client
        self.vndb_client = VNDBClient(http_client)
        self.vndb = VNDBLocalizationAdapter(self.vndb_client)
        self.steam = SteamLocalizationAdapter(http_client)

    def collect(self, news, source, context: CollectionContext) -> CollectionResult:
        host = (urlsplit(source.url).hostname or "").casefold()
        if host == "store.steampowered.com":
            return self.steam.collect(news, source, context)
        explicit_vndb_ref = any(_is_vndb_url(url) for url in [
            *news.source_urls, *(news.localization_context.source_urls if news.localization_context else [])])
        if host in {"vndb.org", "www.vndb.org"} or explicit_vndb_ref:
            return self.vndb.collect(news, source, context)
        return CollectionResult()


def _is_vndb_url(value):
    try:
        parts = urlsplit(value)
        return (parts.scheme == "https" and not parts.username and not parts.password
                and parts.port in {None, 443}
                and (parts.hostname or "").casefold() in {"vndb.org", "www.vndb.org"})
    except (TypeError, ValueError):
        return False
