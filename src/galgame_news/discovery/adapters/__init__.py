"""Source adapters.

The package keeps the historical ``galgame_news.discovery.adapters`` import
surface while grouping HTML, X, and media adapters by responsibility.
"""

from .html import DynamicPageAdapter, OfficialHtmlAdapter, SteamAdapter
from .media import DirectImageAdapter, VideoAdapter
from .x import XAdapter

__all__ = [
    "DirectImageAdapter",
    "DynamicPageAdapter",
    "OfficialHtmlAdapter",
    "SteamAdapter",
    "VideoAdapter",
    "XAdapter",
]
