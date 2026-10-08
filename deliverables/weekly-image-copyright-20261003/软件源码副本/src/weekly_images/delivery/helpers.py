"""Small shared helpers for deterministic delivery and review exports."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path


def atomic_json_write(path: Path, payload, *, default=None, ensure_parent: bool = False) -> None:
    """Write JSON through a sibling temporary file and an atomic replace."""

    path = Path(path)
    if ensure_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            kwargs = {"ensure_ascii": False, "indent": 2}
            if default is not None:
                kwargs["default"] = default
            json.dump(payload, handle, **kwargs)
            handle.write("\n")
        os.replace(name, path)
    except Exception:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def section_label(item) -> str:
    section = (item.section or "news").strip()
    if "\ufffd" in section:
        section = "新作" if item.sequence <= 10 else "其他"
    return section or "news"


def section_prefix(section: str) -> str:
    normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", section).casefold())
    if "新作" in normalized:
        return "x"
    if "汉化" in normalized or "漢化" in normalized:
        return "h"
    if any(
        alias in normalized
        for alias in (
            "周边", "周邊", "周报", "周報", "业界", "業界", "动画", "動畫",
            "旧作", "舊作", "其他", "其它", "资讯", "資訊", "杂项", "雜項",
        )
    ):
        return "z"
    return "z"


def item_names(items) -> dict[str, str]:
    """Return canonical xN/hN/zN folder names in document order."""

    def value(item, *keys, default=None):
        for key in keys:
            if isinstance(item, dict):
                candidate = item.get(key)
            else:
                candidate = getattr(item, key, None)
            if candidate is not None:
                return candidate
        return default

    counters = {"x": 0, "h": 0, "z": 0}
    names: dict[str, str] = {}
    for item in items:
        section_value = value(item, "section", default="news")
        sequence = value(item, "sequence", default=0)
        section = str(section_value or "news").strip() or "news"
        if "\ufffd" in section:
            section = "新作" if int(sequence or 0) <= 10 else "其他"
        prefix = section_prefix(section)
        counters[prefix] += 1
        item_id = value(item, "id", "news_id")
        if item_id:
            names[str(item_id)] = f"{prefix}{counters[prefix]}"
    return names


def safe_title(value, fallback: str) -> str:
    text = str(value or "")
    text = re.sub(r'[\\/:*?"<>|]', "", text).strip().rstrip(".")
    return text or fallback


def safe_video_name(candidate, fallback: str) -> str:
    source = Path(candidate.local_path or "")
    extension = source.suffix or ".mp4"
    raw = candidate.title or source.stem or f"{fallback}_video"
    raw = re.sub(r'[\\/:*?"<>|]', "", raw).strip().rstrip(".")
    return f"{raw or fallback + '_video'}{extension.casefold()}"
