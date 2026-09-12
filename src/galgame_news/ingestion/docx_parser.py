"""OOXML DOCX parser and news-entry segmentation."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from ..domain import IssueDraft, NewsDraft


URL_RE = re.compile(r"https?://", re.IGNORECASE)
HEADING_RE = re.compile(r"^\s*(?P<title>[^#\n]+?)\s*#\s*(?P<author>[^#\n]+?)\s*#?\s*$")
SECTION_HINTS = {
    "新作", "旧作", "舊作", "动画", "動畫", "周边", "周邊", "业界", "業界", "其他", "其它", "汉化", "漢化",
}
PREAMBLE_HINTS = {"目录", "目錄", "索引", "内容", "內容", "周报目录", "周報目錄"}


def parse_docx_paragraphs(path: Path | str) -> list[str]:
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise ValueError(f"无法读取 DOCX：{exc}") from exc
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError(f"DOCX XML 无效：{exc}") from exc
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.iter(namespace + "p"):
        text = "".join(node.text or "" for node in paragraph.iter(namespace + "t"))
        if text.strip():
            paragraphs.append(text.strip())
    return paragraphs


def split_urls(text: str) -> list[str]:
    starts = [match.start() for match in URL_RE.finditer(text)]
    if not starts:
        return []
    result: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        value = text[start:end].strip().rstrip("，。；;、)]】")
        if value:
            result.append(value)
    return result


def _heading(line: str) -> re.Match[str] | None:
    return HEADING_RE.match(line) if "#" in line and not URL_RE.search(line) else None


def _section_label(line: str) -> bool:
    cleaned = line.strip()
    return bool(
        cleaned
        and len(cleaned) <= 16
        and cleaned not in PREAMBLE_HINTS
        and "#" not in cleaned
        and not URL_RE.search(cleaned)
        and not re.search(r"[。！？.!?]$", cleaned)
        and (cleaned in SECTION_HINTS or re.fullmatch(r"[汉漢]化(?:信息|情報|新闻|新聞)?", cleaned))
    )


class DocxDocumentParser:
    """Parse an editorial DOCX without requiring Microsoft Word."""

    def parse(self, path: Path, issue_id: str) -> IssueDraft:
        input_path = Path(path)
        if input_path.suffix.lower() != ".docx" or not input_path.is_file():
            raise ValueError("输入必须是存在的 .docx 文件")
        paragraphs = parse_docx_paragraphs(input_path)
        entries: list[NewsDraft] = []
        section = "新作"
        current: dict | None = None
        for index, line in enumerate(paragraphs):
            heading = _heading(line)
            if heading:
                if current:
                    entries.append(NewsDraft(**current))
                current = {
                    "sequence": len(entries) + 1,
                    "section": section,
                    "title": heading.group("title").strip(),
                    "author": heading.group("author").strip() or None,
                    "body": "",
                    "source_urls": [],
                }
                continue
            next_is_heading = index + 1 < len(paragraphs) and _heading(paragraphs[index + 1])
            if next_is_heading and _section_label(line):
                if current:
                    entries.append(NewsDraft(**current))
                    current = None
                section = line.strip()
                continue
            if current is None:
                # Preamble and unrecognised standalone labels are not news.
                continue
            current["body"] = f"{current['body']}\n{line}".strip()
            current["source_urls"].extend(split_urls(line))
        if current:
            entries.append(NewsDraft(**current))
        for entry in entries:
            entry.source_urls = list(dict.fromkeys(entry.source_urls))
        return IssueDraft(issue_id=issue_id, input_path=str(input_path), entries=entries)
