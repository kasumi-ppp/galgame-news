"""Source discovery and adapter implementations."""

from .adapters import DirectImageAdapter, DynamicPageAdapter, OfficialHtmlAdapter, SteamAdapter, VideoAdapter, XAdapter
from .resolver import DefaultSourceResolver
from .search import BraveSearchProvider, DDGSSearchProvider, FallbackSearchProvider

__all__ = ["DefaultSourceResolver", "DirectImageAdapter", "DynamicPageAdapter", "OfficialHtmlAdapter", "SteamAdapter", "VideoAdapter", "XAdapter", "BraveSearchProvider", "DDGSSearchProvider", "FallbackSearchProvider"]
