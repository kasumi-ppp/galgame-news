"""Atomic JSON/Markdown delivery for pipeline results."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import re
import unicodedata
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..domain import ImageCandidate, ImageType, OutputManifest, PipelineResult, VideoStatus


class OutputManager:
    _SENSITIVE_QUERY_NAMES = {
        "credential", "signature", "token", "api_key", "apikey",
        "access_key", "key", "policy", "expires", "key-pair-id",
    }
    # ``未候选`` is the user-facing canonical name.  Keep the old label as a
    # read-compatibility marker for imported outputs, but never write both
    # directories for one candidate.
    _UNSELECTED_CANDIDATE_DIR = "未候选"
    _LEGACY_FAILED_CANDIDATE_DIR = "失败候选图"
    _FAILED_CANDIDATE_MIN_WIDTH = 300
    _FAILED_CANDIDATE_MIN_HEIGHT = 300
    _FAILED_CANDIDATE_MIN_PIXELS = 120_000
    _FAILED_CANDIDATE_EXCLUDED_TYPES = {ImageType.LOGO, ImageType.BANNER, ImageType.UI}
    _FAILED_CANDIDATE_TYPE_ORDER = {
        ImageType.GAME_CG: 0,
        ImageType.GAMEPLAY_SCREENSHOT: 1,
        ImageType.ANNOUNCEMENT_ART: 2,
        ImageType.KEY_VISUAL: 3,
        ImageType.CHARACTER_ART: 4,
        ImageType.COVER: 5,
        ImageType.GOODS: 6,
        ImageType.PHOTO: 7,
        ImageType.UNKNOWN: 8,
    }

    @classmethod
    def _safe_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.query:
            return value
        safe_query = []
        for key, item_value in parse_qsl(parts.query, keep_blank_values=True):
            normalized = key.casefold()
            if normalized.startswith("x-amz-") or normalized in cls._SENSITIVE_QUERY_NAMES:
                continue
            safe_query.append((key, item_value))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(safe_query, doseq=True), parts.fragment))

    @classmethod
    def _sanitize_payload(cls, value):
        """Remove credentials from serialized provenance without mutating models."""

        if isinstance(value, dict):
            sanitized = {}
            for key, item_value in value.items():
                if isinstance(item_value, str) and str(key).casefold().endswith("url"):
                    sanitized[key] = cls._safe_url(item_value)
                else:
                    sanitized[key] = cls._sanitize_payload(item_value)
            return sanitized
        if isinstance(value, list):
            return [cls._sanitize_payload(item) for item in value]
        if isinstance(value, tuple):
            return [cls._sanitize_payload(item) for item in value]
        return value

    @staticmethod
    def _section_label(item) -> str:
        section = (item.section or "news").strip()
        # Some legacy DOCX files contain replacement characters in section labels;
        # keep sample folders readable while preserving the sequence number.
        if "\ufffd" in section:
            section = "新作" if item.sequence <= 10 else "其他"
        return section or "news"

    @classmethod
    def _section_prefix(cls, section: str) -> str:
        """Map the editorial section aliases to the canonical folder prefix."""

        normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", section).casefold())
        if "新作" in normalized:
            return "x"
        if "汉化" in normalized or "漢化" in normalized:
            return "h"
        # Weekly issues use several labels for the remaining columns.  Both
        # simplified/traditional forms and their common suffixes are accepted.
        if any(
            alias in normalized
            for alias in (
                "周边", "周邊", "周报", "周報", "业界", "業界", "动画", "動畫",
                "旧作", "舊作", "其他", "其它", "资讯", "資訊", "杂项", "雜項",
            )
        ):
            return "z"
        # Every non-new/non-localized item is part of the remaining weekly
        # columns.  Keep unknown labels deterministic and in the z namespace;
        # callers can still preserve the original section in JSON metadata.
        return "z"

    @classmethod
    def item_names(cls, items) -> dict[str, str]:
        """Return canonical xN/hN/zN folder names in document order.

        The helper accepts both domain objects and the dictionaries stored in
        review manifests so raw and final exports share exactly one naming
        policy.
        """

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
        # ``items`` is already the document order.  Number each category by
        # appearance within that category rather than by the global sequence.
        for item in items:
            section_value = value(item, "section", default="news")
            sequence = value(item, "sequence", default=0)
            section = str(section_value or "news").strip() or "news"
            if "\ufffd" in section:
                section = "新作" if int(sequence or 0) <= 10 else "其他"
            prefix = cls._section_prefix(section)
            counters[prefix] += 1
            item_id = value(item, "id", "news_id")
            if item_id:
                names[str(item_id)] = f"{prefix}{counters[prefix]}"
        return names

    # Private compatibility alias used by older delivery callers.
    _item_names = item_names

    def _atomic_json(self, path: Path, payload) -> None:
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
                handle.write("\n")
            os.replace(name, path)
        except Exception:
            try:
                os.unlink(name)
            except OSError:
                pass
            raise

    @classmethod
    def _is_reviewable_failed_candidate(cls, candidate: ImageCandidate) -> bool:
        if candidate.selected or not candidate.downloadable or not candidate.local_path:
            return False
        source = Path(candidate.local_path)
        if not source.is_file() or candidate.image_type in cls._FAILED_CANDIDATE_EXCLUDED_TYPES:
            return False
        width, height = candidate.width or 0, candidate.height or 0
        return (
            width >= cls._FAILED_CANDIDATE_MIN_WIDTH
            and height >= cls._FAILED_CANDIDATE_MIN_HEIGHT
            and width * height >= cls._FAILED_CANDIDATE_MIN_PIXELS
        )

    @classmethod
    def _failed_candidate_sort_key(cls, candidate: ImageCandidate):
        entity_match = candidate.signals.get("entity_match")
        entity_order = 0 if entity_match is True else (2 if entity_match is False else 1)
        raw_type_match = candidate.score.type_match if candidate.score else candidate.signals.get("type_match", 0.0)
        type_match = float(raw_type_match) if isinstance(raw_type_match, (int, float)) else 0.0
        relevance = candidate.score.relevance if candidate.score else 0.0
        total = candidate.score.total if candidate.score else 0.0
        raw_entity_confidence = candidate.signals.get("entity_match_confidence", 0.0)
        entity_confidence = float(raw_entity_confidence) if isinstance(raw_entity_confidence, (int, float)) else 0.0
        pixels = (candidate.width or 0) * (candidate.height or 0)
        return (
            entity_order,
            -relevance,
            -total,
            -type_match,
            -entity_confidence,
            cls._FAILED_CANDIDATE_TYPE_ORDER.get(candidate.image_type, 99),
            -pixels,
            candidate.id or "",
        )

    @staticmethod
    def _candidate_content_key(candidate: ImageCandidate) -> str:
        return candidate.sha256 or candidate.perceptual_hash or candidate.id or candidate.image_url

    @staticmethod
    def _safe_video_name(candidate, fallback: str) -> str:
        source = Path(candidate.local_path or "")
        extension = source.suffix or ".mp4"
        raw = candidate.title or source.stem or f"{fallback}_video"
        raw = re.sub(r'[\\/:*?"<>|]', "", raw).strip().rstrip(".")
        return f"{raw or fallback + '_video'}{extension.casefold()}"

    @classmethod
    def _video_index_payload(cls, result: PipelineResult, videos) -> dict:
        related_news = []
        for item in result.issue.news_items:
            related = [video for video in videos if video.news_id == item.id]
            related_news.append(
                {
                    "news_id": item.id,
                    "sequence": item.sequence,
                    "section": cls._section_label(item),
                    "title": item.title,
                    "videos": [video.id for video in related],
                }
            )
        failures = [
            failure.model_dump(mode="json")
            for failure in result.failures
            if failure.stage.value == "download" and failure.news_id in {video.news_id for video in videos}
        ]
        return cls._sanitize_payload(
            {
                "schema_version": 1,
                "issue_id": result.issue.issue_id,
                "news_items": related_news,
                "videos": [video.model_dump(mode="json") for video in videos],
                "failures": failures,
            }
        )

    def write(self, result: PipelineResult, output_dir: Path | str) -> OutputManifest:
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        image_root = root / "images"
        image_root.mkdir(exist_ok=True)
        # Only remove files managed beneath this run's images directory.
        for child in list(image_root.iterdir()):
            if child.is_symlink():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
            elif child.is_file():
                child.unlink()
        files: list[str] = []
        item_names = self._item_names(result.issue.news_items)
        per_news_rank: dict[str, int] = {}
        for candidate in [c for c in result.candidates if c.selected]:
            if not candidate.local_path or not Path(candidate.local_path).is_file():
                continue
            per_news_rank[candidate.news_id] = per_news_rank.get(candidate.news_id, 0) + 1
            rank = per_news_rank[candidate.news_id]
            readable = re.sub(r"[\\/:*?\"<>|]", "_", item_names.get(candidate.news_id, candidate.news_id))
            target_dir = image_root / readable
            target_dir.mkdir(parents=True, exist_ok=True)
            ext = (candidate.mime_type or "image/jpeg").split("/")[-1].replace("jpeg", "jpg")
            target = target_dir / f"{readable}.{rank:02d}.{ext}"
            source_path = Path(candidate.local_path)
            if source_path.resolve() != target.resolve():
                shutil.copyfile(source_path, target)
            candidate.local_path = str(target)
            files.append(str(target.relative_to(root)))
        all_candidates = result.all_candidates
        exported_failed: set[int] = set()
        for news_id in sorted({candidate.news_id for candidate in all_candidates}):
            failed = sorted(
                (
                    candidate
                    for candidate in all_candidates
                    if candidate.news_id == news_id and self._is_reviewable_failed_candidate(candidate)
                ),
                key=self._failed_candidate_sort_key,
            )
            seen_content: set[str] = set()
            rank = 0
            for candidate in failed:
                content_key = self._candidate_content_key(candidate)
                if content_key in seen_content:
                    continue
                seen_content.add(content_key)
                rank += 1
                readable = re.sub(r"[\\/:*?\"<>|]", "_", item_names.get(news_id, news_id))
                target_dir = image_root / readable / self._UNSELECTED_CANDIDATE_DIR
                target_dir.mkdir(parents=True, exist_ok=True)
                ext = (candidate.mime_type or "image/jpeg").split("/")[-1].replace("jpeg", "jpg")
                target = target_dir / f"{readable}.u{rank:02d}.{ext}"
                source_path = Path(candidate.local_path or "")
                if source_path.resolve() != target.resolve():
                    shutil.copyfile(source_path, target)
                candidate.local_path = str(target)
                exported_failed.add(id(candidate))
                files.append(str(target.relative_to(root)))
        videos = list(getattr(result, "videos", []) or [])
        video_reserved: dict[str, set[str]] = {}
        for video in videos:
            if video.status is not VideoStatus.DOWNLOADED or not video.local_path:
                continue
            source_path = Path(video.local_path)
            if not source_path.is_file():
                continue
            readable = re.sub(r'[\\/:*?"<>|]', "_", item_names.get(video.news_id, video.news_id))
            target_dir = image_root / readable
            target_dir.mkdir(parents=True, exist_ok=True)
            target_name = self._safe_video_name(video, readable)
            reserved = video_reserved.setdefault(video.news_id, set())
            stem, suffix = Path(target_name).stem, Path(target_name).suffix
            index = 2
            while target_name.casefold() in reserved or (target_dir / target_name).exists():
                target_name = f"{stem} ({index}){suffix}"
                index += 1
            reserved.add(target_name.casefold())
            target = target_dir / target_name
            if source_path.resolve() != target.resolve():
                shutil.copyfile(source_path, target)
            video.local_path = str(target)
            files.append(str(target.relative_to(root)))

        for candidate in all_candidates:
            if not candidate.selected and id(candidate) not in exported_failed:
                candidate.local_path = None
        candidate_payload = self._sanitize_payload([candidate.model_dump(mode="json") for candidate in all_candidates])
        news_payload = []
        for item in result.issue.news_items:
            related = [candidate for candidate in all_candidates if candidate.news_id == item.id]
            status = "selected" if any(c.selected for c in related) else ("failed" if any(f.news_id == item.id for f in result.failures) else ("no_candidate" if not related else "not_selected"))
            news_payload.append({"news_id": item.id, "sequence": item.sequence, "section": self._section_label(item), "title": item.title, "status": status, "candidates": [c.id for c in related]})
        failures_payload = self._sanitize_payload([failure.model_dump(mode="json") for failure in result.failures])
        index = {"schema_version": 1, "issue_id": result.issue.issue_id, "news_items": news_payload, "candidates": candidate_payload, "failures": failures_payload}
        self._atomic_json(root / "image_index.json", index)
        self._atomic_json(root / "video_index.json", self._video_index_payload(result, videos))
        self._atomic_json(root / "failed_items.json", failures_payload)
        reviews = result.review_required or [c for c in all_candidates if c.review_reasons]
        reviews_payload = self._sanitize_payload([candidate.model_dump(mode="json") for candidate in reviews])
        self._atomic_json(root / "review_required.json", reviews_payload)
        lines = [f"# Issue {result.issue.issue_id}", "", f"候选图片：{len(all_candidates)} 张", ""]
        for item in news_payload:
            lines.append(f"- {item['sequence']}. {item['title']} — {item['status']} ({len(item['candidates'])} candidates)")
        (root / "image_index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return OutputManifest(issue_id=result.issue.issue_id, output_dir=str(root), image_count=sum(c.selected for c in result.candidates), files=files)
