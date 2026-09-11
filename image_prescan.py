"""Offline-friendly weekly galgame-news image prescan."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import math
import re
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

try:
    from PIL import Image
except ImportError:  # exact-byte dedupe remains available without Pillow
    Image = None


USER_AGENT = "WeeklyGalgameImagePrescan/1.0 (+local editorial review)"
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BYTES = 12 * 1024 * 1024
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif")
URL_RE = re.compile(r"https?://", re.I)
HEADING_RE = re.compile(r"^\s*(?P<title>[^#\n]+?)\s*#\s*(?P<author>[^#\n]+?)\s*#?\s*$")
NUMBER_WORDS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
NUMBER_UNITS = {"十": 10, "百": 100, "千": 1000}
COUNTERS = "张張枚点點幅个個"
COUNT_TOKEN_RE = r"[0-9０-９]+|[〇零一二三四五六七八九十百千兩两]+"
ANNOUNCEMENT_RE = re.compile(r"更新|公开|公開|公布|新增|追加|发布|發佈|發布|释出|釋出|展示|附上|解禁|登场|登場|掲載|配信|披露|new", re.I)
FEATURE_DESCRIPTION_RE = re.compile(r"(?:事件|イベント)?\s*cg\s*(?:鉴赏|鑑賞|模式)", re.I)
SENTENCE_SPLIT_RE = re.compile(r"[。！？!?\n]+")
UNAVAILABLE_LANGUAGE_RE = (
    r"尚未|暂未|暫未|还未|還未|没有|沒有|不会|不會|"
    r"即将|即將|将在|將在|将于|將於|将要|將要|计划|計劃|预计|預計|预定|預定|打算|"
    r"明天|明日|后天|後天|下(?:周|週|个月|個月|月)|稍后|稍後"
)
IMAGE_NOUNS = (
    ("key_visual", re.compile(r"キービジュアル|key\s+visual|主(?:视觉|視覺)(?:图|圖)?|视觉图|視覺圖", re.I)),
    ("event_cg", re.compile(r"(?:事件|イベント)?\s*cg", re.I)),
    ("background", re.compile(r"背景(?:图片|圖片|画|畫)?")),
    ("illustration", re.compile(r"插图|插畫|イラスト|贺图|賀圖")),
)
SECTION_HINTS = {"新作", "旧作", "舊作", "动画", "動畫", "周边", "周邊", "业界", "業界", "其他", "其它", "汉化", "漢化"}


def parse_docx_paragraphs(path: Path | str) -> list[str]:
    """Read document paragraphs directly from OOXML, without Word or packages."""
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise ValueError(f"无法读取 DOCX：{exc}") from exc
    root = ET.fromstring(xml)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(namespace + "p"):
        text = "".join(node.text or "" for node in paragraph.iter(namespace + "t"))
        if text.strip():
            paragraphs.append(text.strip())
    return paragraphs


def split_urls(text: str) -> list[str]:
    """Split URLs glued together in an editorial document."""
    starts = [match.start() for match in URL_RE.finditer(text)]
    if not starts:
        return []
    return [text[start:ends].strip() for start, ends in zip(starts, starts[1:] + [len(text)]) if text[start:ends].strip()]


def _is_heading(line: str) -> re.Match[str] | None:
    return HEADING_RE.match(line) if "#" in line and not URL_RE.search(line) else None


def _is_section_label(line: str) -> bool:
    """Section labels are short standalone text, never a URL or a contributor heading."""
    cleaned = line.strip()
    return bool(cleaned and len(cleaned) <= 12 and "#" not in cleaned and not URL_RE.search(cleaned) and not re.search(r"[。！？.!?]$", cleaned) and (cleaned in SECTION_HINTS or re.fullmatch(r"[汉漢]化(?:信息|情報|新闻|新聞)?", cleaned)))


def segment_articles(paragraphs: list[str]) -> list[dict]:
    """Associate headings, paragraphs, URLs, and their nearest section label."""
    articles: list[dict] = []
    section = "新作"
    current: dict | None = None
    for index, line in enumerate(paragraphs):
        heading = _is_heading(line)
        if heading:
            if current:
                articles.append(current)
            current = {
                "index": len(articles) + 1,
                "section": section,
                "title": heading.group("title").strip(),
                "author": heading.group("author").strip(),
                "text": [],
                "source_urls": [],
            }
            continue
        next_is_heading = index + 1 < len(paragraphs) and _is_heading(paragraphs[index + 1])
        if next_is_heading and (_is_section_label(line) or (not current and not URL_RE.search(line) and "#" not in line)):
            if current:
                articles.append(current)
                current = None
            section = line.strip()
            continue
        if current:
            current["text"].append(line)
            current["source_urls"].extend(split_urls(line))
    if current:
        articles.append(current)
    for article in articles:
        article["body"] = "\n".join(article.pop("text"))
        article["source_urls"] = list(dict.fromkeys(article["source_urls"]))
    return articles


def _parse_count(value: str) -> int:
    normalized = value.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if normalized.isdigit():
        return int(normalized)
    total = 0
    current = 0
    for char in normalized:
        if char in NUMBER_WORDS:
            current = NUMBER_WORDS[char]
        elif char in NUMBER_UNITS:
            total += (current or 1) * NUMBER_UNITS[char]
            current = 0
    return total + current


def _noun_count(text: str, noun: re.Match[str]) -> int | None:
    """Return only a counter adjoining this noun; unrelated sentence numbers are ignored."""
    before = re.split(r"[，,；;。！？!?\n]", text[max(0, noun.start() - 32):noun.start()])[-1]
    after = re.split(r"[，,；;。！？!?\n]", text[noun.end():noun.end() + 32], maxsplit=1)[0]
    preceding = re.search(rf"(?P<count>{COUNT_TOKEN_RE})\s*[{COUNTERS}](?:\s*(?:新|新規|新规))?\s*$", before)
    if preceding:
        return _parse_count(preceding.group("count"))
    following = re.match(rf"\s*(?:(?:{ANNOUNCEMENT_RE.pattern})(?:了)?)?\s*(?P<count>{COUNT_TOKEN_RE})\s*[{COUNTERS}]", after, re.I)
    if following:
        return _parse_count(following.group("count"))
    return None


def _expected_count(text: str, noun: re.Match[str]) -> int:
    """Read a counter immediately adjoining the announced image noun, never a stray year."""
    return _noun_count(text, noun) or 1


def _publication_is_current(clause: str, publication: re.Match[str]) -> bool:
    """Reject explicit negation/schedules attached to this particular action."""
    modifiers = re.split(r"但是|不过|不過|但|另外|并且|並且|并|並", clause[:publication.start()])[-1]
    return not re.search(
        rf"{UNAVAILABLE_LANGUAGE_RE}|未\s*$|不(?:会|會|再|曾)?\s*$|将\s*$|將\s*$",
        modifiers,
    )


def _has_image_evidence(sentence: str, noun: re.Match[str]) -> bool:
    """Bind an action to this image, or to an explicitly elaborated news update."""
    action = ANNOUNCEMENT_RE.pattern
    # Semicolons end an association; a comma can carry an explicit elaboration.
    prefix = re.split(r"[；;]", sentence[:noun.start()])[-1]
    clauses = re.split(r"[，,]", prefix)
    local_prefix = clauses[-1]
    unrelated_object = r"发售日|發售日|体验版|體驗版|系统介绍|系統介紹|收录|收錄|内含|內含|包含|包括|其中|情报|情報|内容|內容|介绍|介紹"
    for publication in ANNOUNCEMENT_RE.finditer(local_prefix):
        if not _publication_is_current(local_prefix, publication):
            continue
        target = local_prefix[publication.end():]
        if re.search(unrelated_object, target):
            continue
        if _noun_count(sentence, noun) is not None or re.fullmatch(r"(?:了|新|新的|最新)?\s*", target):
            return True

    # Reverse constructions such as 背景图片新增2枚 must be immediate.
    suffix = re.split(r"[，,；;]", sentence[noun.end():], maxsplit=1)[0]
    if re.match(rf"\s*(?:{action})", suffix, re.I):
        return True

    if len(clauses) < 2:
        return False
    update_object = r"(?:更新内容|更新內容|新情报|新情報)"
    introduced_update = re.search(rf"(?:{action})(?:了)?\s*{update_object}\s*$", clauses[-2], re.I)
    image_elaboration = re.fullmatch(
        rf"\s*(?:包括|其中(?:有|包括|包含))\s*(?:{COUNT_TOKEN_RE})\s*[{COUNTERS}](?:新|新規|新规)?\s*",
        local_prefix,
    )
    return bool(introduced_update and _publication_is_current(clauses[-2], introduced_update) and image_elaboration)


def detect_image_intent(text: str) -> tuple[str, int]:
    """Return the announced still-image category and count, never infer from unrelated news."""
    for sentence in SENTENCE_SPLIT_RE.split(text):
        happy_weekend = re.search(r"happy\s+weekend[^。！？\n]*(?:贺图|賀圖)", sentence, re.I)
        if happy_weekend:
            suffix = sentence[happy_weekend.end():]
            if re.match(
                rf"[^，,；;。！？]{{0,20}}(?:{UNAVAILABLE_LANGUAGE_RE}|未(?=公开|公開|发布|發布|更新))",
                suffix,
            ):
                continue
            publications = list(ANNOUNCEMENT_RE.finditer(sentence[:happy_weekend.end()]))
            if publications and _publication_is_current(sentence, publications[-1]):
                return "illustration", 1
            if not publications and not re.search(UNAVAILABLE_LANGUAGE_RE, sentence):
                return "illustration", 1
    cleaned = FEATURE_DESCRIPTION_RE.sub("", text)
    for sentence in SENTENCE_SPLIT_RE.split(cleaned):
        if not ANNOUNCEMENT_RE.search(sentence):
            continue
        for category, pattern in IMAGE_NOUNS:
            for noun in pattern.finditer(sentence):
                if _has_image_evidence(sentence, noun):
                    return category, _expected_count(sentence, noun)
    return "none", 0


class _CandidateParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.items: list[dict] = []

    def _add(self, value: str | None, attrs: dict[str, str], origin: str) -> None:
        if not value or value.startswith("data:"):
            return
        value = html.unescape(value.strip().split(",")[0].strip().split(" ")[0])
        try:
            url = urllib.parse.urljoin(self.base_url, value)
        except ValueError:
            return
        if not url.startswith(("http://", "https://")):
            return
        detail = " ".join([url, origin] + list(attrs.values())).lower()
        positive = ("cg", "event", "scene", "gallery", "illustr", "visual", "news", "character", "image", "sample", "screenshot")
        negative = ("logo", "icon", "banner", "nav", "header", "footer", "sprite", "button", "share", "twitter")
        score = sum(15 for hint in positive if hint in detail) - sum(20 for hint in negative if hint in detail)
        if origin in ("og", "twitter"):
            score += 8
        self.items.append({"url": url, "origin": origin, "score": score, "attributes": attrs})

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {key.lower(): value or "" for key, value in attrs_list}
        if tag == "meta" and attrs.get("property", "").lower() in ("og:image", "og:image:url"):
            self._add(attrs.get("content"), attrs, "og")
        elif tag == "meta" and attrs.get("name", "").lower() in ("twitter:image", "twitter:image:src"):
            self._add(attrs.get("content"), attrs, "twitter")
        elif tag == "img":
            for name in ("src", "data-src", "data-lazy-src", "data-original", "data-image"):
                self._add(attrs.get(name), attrs, name)
            if attrs.get("srcset"):
                for source in attrs["srcset"].split(","):
                    self._add(source.strip(), attrs, "srcset")
        elif tag == "a" and any(attrs.get("href", "").lower().split("?")[0].endswith(ext) for ext in IMAGE_EXTENSIONS):
            self._add(attrs.get("href"), attrs, "anchor")


def extract_candidates(page_html: str, base_url: str) -> list[dict]:
    parser = _CandidateParser(base_url)
    parser.feed(page_html)
    deduplicated: dict[str, dict] = {}
    for candidate in parser.items:
        prior = deduplicated.get(candidate["url"])
        if prior is None or candidate["score"] > prior["score"]:
            deduplicated[candidate["url"]] = candidate
    return sorted(deduplicated.values(), key=lambda item: (-item["score"], item["url"]))


def _fetch(url: str, timeout: float = 12, retries: int = 2, max_bytes: int = MAX_RESPONSE_BYTES) -> tuple[bytes, str]:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,image/*;q=0.8"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > max_bytes:
                    raise ValueError("response_too_large")
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ValueError("response_too_large")
                return data, response.headers.get_content_type()
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.2 * (attempt + 1))
    raise RuntimeError(str(last_error or "fetch_failed"))


def inspect_source(url: str, *, fetcher: Callable | None = None, timeout: float = 12) -> dict:
    """Inspect only official supplied URLs; uncertain pages always retain a human decision."""
    result = {"source_url": url, "status": "checked", "candidates": [], "manual_review": [], "error": None}
    try:
        hostname = urllib.parse.urlparse(url).hostname or ""
    except ValueError as exc:
        result.update(status="manual_review", manual_review=["invalid_source_url"], error=str(exc))
        return result
    if hostname.lower() in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        result.update(status="manual_review", manual_review=["x_source_unavailable"])
        return result
    try:
        response = fetcher(url, timeout) if fetcher else _fetch(url, timeout=timeout)
        raw = response[0] if isinstance(response, tuple) else response
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    except Exception as exc:  # individual source errors are non-fatal
        result.update(status="manual_review", manual_review=["source_fetch_failed"], error=str(exc))
        return result
    lower = text.lower()
    if any(marker in lower for marker in ("age gate", "age-gate", "年齢確認", "成人向け", "18禁", "r18")):
        result.update(status="manual_review", manual_review=["age_gated"])
        return result
    result["candidates"] = extract_candidates(text, url)
    dynamic = "wix" in hostname.lower() or "__next" in lower or "id=\"app\"" in lower or "id='app'" in lower
    if dynamic and not result["candidates"]:
        result.update(status="manual_review", manual_review=["dynamic_page_no_static_image"])
    elif not result["candidates"]:
        result.update(status="manual_review", manual_review=["no_static_image_candidate"])
    return result


def image_dimensions(data: bytes) -> dict | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return {"width": width, "height": height}
    if data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        return {"width": width, "height": height}
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP" and len(data) >= 30:
        kind = data[12:16]
        if kind == b"VP8X":
            return {"width": 1 + int.from_bytes(data[24:27], "little"), "height": 1 + int.from_bytes(data[27:30], "little")}
        if kind == b"VP8 " and len(data) >= 30:
            return {"width": struct.unpack("<H", data[26:28])[0] & 0x3FFF, "height": struct.unpack("<H", data[28:30])[0] & 0x3FFF}
        if kind == b"VP8L" and len(data) >= 25:
            bits = int.from_bytes(data[21:25], "little")
            return {"width": (bits & 0x3FFF) + 1, "height": ((bits >> 14) & 0x3FFF) + 1}
    if data.startswith(b"\xff\xd8"):
        position = 2
        while position + 9 < len(data):
            if data[position] != 0xFF:
                position += 1
                continue
            marker = data[position + 1]
            length = struct.unpack(">H", data[position + 2:position + 4])[0]
            if marker in range(0xC0, 0xC4) or marker in range(0xC5, 0xC8) or marker in range(0xC9, 0xCC) or marker in range(0xCD, 0xD0):
                height, width = struct.unpack(">HH", data[position + 5:position + 9])
                return {"width": width, "height": height}
            position += 2 + length
    return None


def _is_image(data: bytes, content_type: str) -> bool:
    if data.startswith(b"\xff\xd8"):
        return image_dimensions(data) is not None and data.rfind(b"\xff\xd9") >= 2
    return data.startswith((b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a")) or (data.startswith(b"RIFF") and data[8:12] == b"WEBP") or (len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in (b"avif", b"avis"))


def _safe_name(value: str) -> str:
    name = re.sub(r"[<>:\\/*?\"|\x00-\x1f]", "_", value).strip(" ._")
    return (name or "article")[:80]


def _perceptual_fingerprint(data: bytes) -> dict | None:
    """Make a conservative resize-resistant fingerprint when Pillow is available."""
    if Image is None:
        return None
    try:
        with Image.open(io.BytesIO(data)) as opened:
            image = opened.convert("RGB")
            width, height = image.size
            sample = image.resize((9, 8), Image.Resampling.LANCZOS)
            pixels = [sample.getpixel((column, row)) for row in range(8) for column in range(9)]
    except Exception:
        return None
    gray = [(r * 299 + g * 587 + b * 114) // 1000 for r, g, b in pixels]
    dhash = 0
    for row in range(8):
        for column in range(8):
            dhash = (dhash << 1) | (gray[row * 9 + column] > gray[row * 9 + column + 1])
    averages = tuple(sum(pixel[channel] for pixel in pixels) // len(pixels) for channel in range(3))
    return {"dhash": dhash, "average_rgb": averages, "aspect": width / height if height else 0}


def _same_visual(left: dict, right: dict) -> bool:
    if not left["aspect"] or abs(left["aspect"] - right["aspect"]) > max(left["aspect"], right["aspect"]) * 0.03:
        return False
    color_distance = sum((a - b) ** 2 for a, b in zip(left["average_rgb"], right["average_rgb"])) ** 0.5
    return (left["dhash"] ^ right["dhash"]).bit_count() <= 5 and color_distance <= 35


def download_candidates(candidates: list[dict], directory: Path | str, *, downloader: Callable | None = None, timeout: float = 12, max_candidates: int = 4, known_hashes: dict[str, str] | None = None, known_perceptual: list[tuple[dict, str]] | None = None) -> list[dict]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    results = []
    known_hashes = known_hashes if known_hashes is not None else {}
    known_perceptual = known_perceptual if known_perceptual is not None else []
    for position, candidate in enumerate(candidates[:max_candidates], 1):
        url = candidate["url"]
        try:
            response = downloader(url, timeout) if downloader else _fetch(url, timeout=timeout, max_bytes=MAX_IMAGE_BYTES)
            data, content_type = response
            if len(data) > MAX_IMAGE_BYTES or not _is_image(data, content_type):
                raise ValueError("not_a_bounded_image")
            digest = hashlib.sha256(data).hexdigest()
            suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
            if suffix not in IMAGE_EXTENSIONS:
                suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}.get(content_type, ".img")
            fingerprint = _perceptual_fingerprint(data)
            duplicate_kind = "exact" if digest in known_hashes else None
            existing_file = known_hashes.get(digest)
            if existing_file is None and fingerprint is not None:
                for prior_fingerprint, prior_file in known_perceptual:
                    if _same_visual(fingerprint, prior_fingerprint):
                        duplicate_kind = "perceptual"
                        existing_file = prior_file
                        break
            if existing_file is None:
                filename = f"{position:02d}_{digest[:12]}{suffix}"
                destination = directory / filename
                destination.write_bytes(data)
                existing_file = str(destination)
                known_hashes[digest] = existing_file
                if fingerprint is not None:
                    known_perceptual.append((fingerprint, existing_file))
            results.append({**candidate, "original_image_url": url, "sha256": digest, "file": existing_file, "duplicate": duplicate_kind is not None, "duplicate_kind": duplicate_kind, "dimensions": image_dimensions(data), "content_type": content_type, "downloaded": True})
        except Exception as exc:
            results.append({**candidate, "original_image_url": url, "downloaded": False, "error": str(exc)})
    return results


def _write_outputs(output: Path, manifest: dict) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = ["article_index", "section", "title", "status", "source_url", "original_image_url", "score", "confidence", "safety_status", "manual_review_reasons", "duplicate", "duplicate_kind", "file"]
    with (output / "review.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for article in manifest["articles"]:
            candidates = article["candidates"] or [{}]
            for candidate in candidates:
                writer.writerow({"article_index": article["index"], "section": article["section"], "title": article["title"], "status": article["status"], "source_url": candidate.get("source_url", "; ".join(article["source_urls"])), "original_image_url": candidate.get("original_image_url", candidate.get("url", "")), "score": candidate.get("score", ""), "confidence": article["confidence"], "safety_status": article["safety_status"], "manual_review_reasons": "; ".join(article["manual_review_reasons"]), "duplicate": candidate.get("duplicate", ""), "duplicate_kind": candidate.get("duplicate_kind", ""), "file": candidate.get("file", "")})
    totals = manifest["totals"]
    report = f"# 图片预检报告\n\n处理文章：{totals['articles']} 篇；待人工复核：{totals['manual_review']} 篇；无新增静态图：{totals['no_new_image']} 篇。\n\n请在 `review.csv` 和文章目录中确认图片是否正是本次新增图片，并人工审核 R18/年龄限制内容。\n"
    (output / "report.md").write_text(report, encoding="utf-8")


def run_prescan(input_docx: Path | str, *, output: Path | str | None = None, section: str = "新作", max_candidates: int = 4, timeout: float = 12, offline: bool = False) -> Path:
    if max_candidates < 1 or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("max_candidates 和 timeout 必须是有限正数")
    input_docx = Path(input_docx)
    if input_docx.suffix.lower() != ".docx" or not input_docx.is_file():
        raise ValueError("输入必须是存在的 .docx 文件")
    output_path = Path(output) if output else Path("output") / f"image_prescan_{_safe_name(input_docx.stem)}"
    parsed = segment_articles(parse_docx_paragraphs(input_docx))
    selected = [article for article in parsed if article["section"] == section]
    records = []
    known_hashes: dict[str, str] = {}
    known_perceptual: list[tuple[dict, str]] = []
    for article in selected:
        intent, count = detect_image_intent(article["body"])
        record = {**article, "image_intent": intent, "expected_image_count": count, "source_attempts": [], "candidates": [], "confidence": "none", "safety_status": "not_assessed", "manual_review_reasons": []}
        if intent == "none":
            record["status"] = "no_new_image"
        elif offline:
            record.update(status="manual_review", manual_review_reasons=["offline_source_not_checked"], confidence="low")
        elif not article["source_urls"]:
            record.update(status="manual_review", manual_review_reasons=["no_source_url"], confidence="low")
        else:
            for source_url in article["source_urls"]:
                attempt = inspect_source(source_url, timeout=timeout)
                record["source_attempts"].append(attempt)
                for candidate in attempt["candidates"][:max_candidates]:
                    record["candidates"].append({**candidate, "source_url": source_url})
                record["manual_review_reasons"].extend(attempt["manual_review"])
            record["manual_review_reasons"] = list(dict.fromkeys(record["manual_review_reasons"]))
            if record["candidates"]:
                directory = output_path / "images" / f"{article['index']:02d}_{_safe_name(article['title'])}"
                record["candidates"] = download_candidates(record["candidates"], directory, timeout=timeout, max_candidates=max_candidates, known_hashes=known_hashes, known_perceptual=known_perceptual)
            record["safety_status"] = "manual_content_review_required"
            record["status"] = "manual_review" if record["manual_review_reasons"] else "candidate_downloaded"
            record["confidence"] = "low" if record["manual_review_reasons"] else "candidate_only"
            if not record["manual_review_reasons"]:
                record["manual_review_reasons"] = ["exact_new_image_not_confirmed"]
                record["status"] = "manual_review"
        records.append(record)
    totals = {"articles": len(records), "manual_review": sum(item["status"] == "manual_review" for item in records), "no_new_image": sum(item["status"] == "no_new_image" for item in records), "candidates": sum(len(item["candidates"]) for item in records)}
    manifest = {"run": {"input": str(input_docx), "section": section, "offline": offline, "created_at": datetime.now(timezone.utc).isoformat(), "max_candidates": max_candidates, "timeout_seconds": timeout, "perceptual_dedupe_available": Image is not None}, "articles": records, "totals": totals}
    _write_outputs(output_path, manifest)
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Galgame weekly-news image prescan")
    parser.add_argument("input_docx", metavar="INPUT.docx")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--section", default="新作")
    parser.add_argument("--max-candidates", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=12)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args(argv)
    if args.max_candidates < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--max-candidates 必须大于 0，--timeout 必须是有限正数")
    try:
        result = run_prescan(args.input_docx, output=args.output, section=args.section, max_candidates=args.max_candidates, timeout=args.timeout, offline=args.offline)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"致命错误：{exc}", file=sys.stderr)
        return 1
    print(f"已生成：{result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
