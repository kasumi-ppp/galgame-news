"""Bounded extraction of explicit gallery image lists from JavaScript sources.

This intentionally recognizes a few declarative shapes. It never evaluates
JavaScript and ignores computed programs whose output cannot be proven from a
small literal list or a finite, numeric-index template.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup


MAX_GALLERY_IMAGES = 500
MAX_SCRIPTS_PER_PAGE = 4


@dataclass(frozen=True)
class GalleryScriptImage:
    url: str
    item_id: str
    relationship: str
    thumbnail_url: str | None = None


def _gallery_targets_linked(soup: BeautifulSoup) -> bool:
    track = soup.find(id="galleryThumbnailTrack")
    preview = soup.find(id="galleryPreview")
    if track is None or preview is None:
        return False
    ancestors = set(track.parents) & set(preview.parents)
    for node in ancestors:
        if not getattr(node, "name", None) or node.name in {"html", "body"}:
            continue
        tokens = " ".join([str(node.get("id") or ""), *map(str, node.get("class") or [])])
        if re.search(r"gallery", tokens, re.I):
            return True
    return False


def gallery_script_sources(soup: BeautifulSoup, page_url: str) -> list[str]:
    """Return at most four associated script URLs, in document order."""
    page_host = (urlsplit(page_url).hostname or "").casefold()
    result: list[str] = []
    for script in soup.find_all("script", src=True):
        raw = str(script.get("src") or "")
        url = urljoin(page_url, raw)
        parts = urlsplit(url)
        host = (parts.hostname or "").casefold()
        if parts.scheme not in {"http", "https"} or not host:
            continue
        same_host = host == page_host
        explicitly_linked = bool(script.get("data-gallery") or "gallery" in str(script.get("id") or "").casefold())
        if not (same_host or explicitly_linked):
            continue
        if "gallery" not in (raw + " " + str(script.get("id") or "")).casefold() and not script.get("data-gallery"):
            continue
        if url not in result:
            result.append(url)
        if len(result) >= MAX_SCRIPTS_PER_PAGE:
            break
    return result


def _literal_images(script: str) -> list[dict[str, object]]:
    match = re.search(r"(?:const|let|var)\s+images\s*=\s*(\[[\s\S]*?\])\s*;", script)
    if not match:
        return []
    literal = match.group(1)
    if len(literal) > 256_000:
        return []
    try:
        value = json.loads(literal)
    except (ValueError, TypeError):
        try:
            literal = re.sub(r"([{,]\s*)([A-Za-z_$][\w$]*)\s*:", r"\1'\2':", literal)
            value = ast.literal_eval(literal)
        except (ValueError, SyntaxError):
            return []
    if not isinstance(value, list) or len(value) > MAX_GALLERY_IMAGES:
        return []
    return [row if isinstance(row, dict) else {"preview": row} for row in value if isinstance(row, (dict, str))]


def _array_from_templates(script: str) -> list[dict[str, object]]:
    # Keep this grammar narrow: a named count, Array.from's length, a numeric
    # zero-based index incremented by one, and a returned object literal.
    count_match = re.search(r"(?:const|let|var)\s+imageCount\s*=\s*(\d+)\s*;", script)
    if not count_match:
        return []
    count = int(count_match.group(1))
    if not 1 <= count <= MAX_GALLERY_IMAGES:
        return []
    array = re.search(r"(?:const|let|var)\s+images\s*=\s*Array\.from\s*\(\s*\{\s*length\s*:\s*imageCount\s*\}\s*,\s*\(\s*_\s*,\s*index\s*\)\s*=>\s*\{([\s\S]*?)\}\s*\)\s*;", script)
    if not array:
        return []
    script = array.group(1)
    if not re.search(r"\bindex\s*\+\s*1\b", script):
        return []
    if not re.search(r"\breturn\s*\{", script):
        return []
    object_start = re.search(r"\breturn\s*\{", script)
    object_end = re.search(r"\n\s*\};?\s*\n", script[object_start.end():]) if object_start else None
    if not object_start:
        return []
    body = script[object_start.end():object_start.end() + object_end.start()] if object_end else script[object_start.end():]
    fields = {}
    for key, template in re.findall(r"\b([A-Za-z_$][\w$]*)\s*:\s*`([^`]{1,500})`", body):
        if "${" not in template:
            continue
        fields[key] = template
    if any("${number}" in value.replace(" ", "") for value in fields.values()) and not re.search(r"(?:const|let|var)\s+number\s*=\s*index\s*\+\s*1\s*;", script):
        return []
    if len(re.findall(r"\bnumber\s*=(?!=)", script)) > 1:
        return []
    if not fields:
        return []
    rows = []
    for number in range(1, count + 1):
        row = {}
        for key, template in fields.items():
            value = re.sub(r"\$\{\s*(?:number|index\s*\+\s*1)\s*\}", str(number), template)
            # Any remaining interpolation means this field is not understood.
            if "${" not in value:
                row[key] = value
        rows.append(row)
    return rows


def parse_gallery_script(script: str, page_url: str, soup: BeautifulSoup) -> list[GalleryScriptImage]:
    """Extract gallery previews only when script and DOM targets corroborate."""
    if not _gallery_targets_linked(soup):
        return []
    lowered = script.casefold()
    if "gallerythumbnailtrack" not in lowered or "gallerypreview" not in lowered:
        return []
    rows = _array_from_templates(script) or _literal_images(script)
    result = []
    seen = set()
    for index, row in enumerate(rows, 1):
        raw = next((row.get(key) for key in ("preview", "image", "src", "url") if isinstance(row.get(key), str)), None)
        if not raw:
            continue
        url = urljoin(page_url, str(raw))
        if urlsplit(url).scheme not in {"http", "https"} or url in seen:
            continue
        seen.add(url)
        thumbnail = row.get("thumbnail")
        thumbnail_url = urljoin(page_url, thumbnail) if isinstance(thumbnail, str) else None
        if thumbnail_url and urlsplit(thumbnail_url).scheme not in {"http", "https"}:
            thumbnail_url = None
        result.append(GalleryScriptImage(url=url, item_id=f"gallery-{index}", relationship="galleryPreview.src from gallery image list", thumbnail_url=thumbnail_url))
    return result
