"""Defensive review state loading and non-destructive final export."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from ..domain import (
    ImageCandidate,
    ImageType,
    ScoreBreakdown,
    SourceType,
    VideoCandidate,
    VideoStatus,
    candidate_id_for,
    video_candidate_id_for,
)
from ..delivery.output import OutputManager


class ReviewDecision(str, Enum):
    ACCEPTED = "accepted"
    PENDING = "pending"
    REJECTED = "rejected"


@dataclass
class RetryMergeResult:
    """Description of one retry merge applied to a review session."""

    news_id: str
    attempt_id: str
    added_image_ids: list[str]
    added_video_ids: list[str]

    @property
    def added_ids(self) -> list[str]:
        return [*self.added_image_ids, *self.added_video_ids]


def _read_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return default
    return value


def _as_dict(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) else None


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.now(timezone.utc)
    else:
        parsed = datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _clamp(value: Any, low: float, high: float, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _stable_fallback(payload: dict[str, Any], prefix: str) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:16]}"


def _items(payload: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _review_ids(payload: Any) -> set[str]:
    result: set[str] = set()
    values = payload if isinstance(payload, list) else payload.get("candidates", []) if isinstance(payload, dict) else []
    if not isinstance(values, list):
        return result
    for value in values:
        if isinstance(value, str):
            result.add(value)
        elif isinstance(value, dict) and value.get("id") is not None:
            result.add(str(value["id"]))
    return result


def _safe_title(value: Any, fallback: str) -> str:
    text = str(value or "")
    text = re.sub(r'[\\/:*?"<>|]', "", text).strip().rstrip(".")
    return text or fallback


class ReviewSession:
    """Review images/videos from one output directory.

    All source indexes are read-only.  Review decisions are stored in the
    separately managed state path, and final export copies accepted files into
    the task directory.
    """

    _LOW_QUALITY_MIN_WIDTH = 300
    _LOW_QUALITY_MIN_HEIGHT = 300
    _LOW_QUALITY_MIN_PIXELS = 120_000
    _HARD_IMAGE_TYPES = {"logo", "favicon", "icon", "ui", "banner"}

    def __init__(
        self,
        output_dir: Path,
        *,
        task_root: Path | None,
        state_path: Path,
        images: list[ImageCandidate],
        videos: list[VideoCandidate],
        news_items: list[dict[str, Any]],
        source_paths: dict[str, Path],
        raw_types: dict[str, str],
        review_ids: set[str],
        failed_ids: set[str],
    ):
        self.output_dir = output_dir
        self.task_root = task_root or state_path.parent
        self.state_path = state_path
        self.images = images
        self.videos = videos
        self.news_items = news_items
        self._source_paths = source_paths
        self._raw_types = raw_types
        self._review_ids = review_ids
        self._failed_ids = failed_ids
        self._decisions: dict[str, ReviewDecision] = {}
        self._image_by_id = {str(item.id): item for item in images}
        self._video_by_id = {str(item.id): item for item in videos}

    @classmethod
    def from_output(
        cls,
        output_dir: Path | str,
        *,
        state_path: Path | str | None = None,
        task_root: Path | str | None = None,
    ) -> "ReviewSession":
        root = Path(output_dir).expanduser()
        if root.is_file():
            root = root.parent
        managed_root = Path(task_root).expanduser() if task_root is not None else None
        if state_path is not None:
            state = Path(state_path).expanduser()
        elif managed_root is not None:
            state = managed_root / "review_state.json"
        else:
            # A sibling path is important for imported legacy output: setting a
            # decision must never create a file inside the legacy directory.
            state = root.parent / f".{root.name}.review_state.json"

        image_payload = _read_json(root / "image_index.json", {})
        video_payload = _read_json(root / "video_index.json", {})
        review_payload = _read_json(root / "review_required.json", [])
        failed_payload = _read_json(root / "failed_items.json", [])
        image_items = _items(image_payload, ("candidates", "images", "image_candidates"))
        video_items = _items(video_payload, ("videos", "video_candidates", "candidates"))
        news_items = cls._news_items(image_payload, video_payload, image_items, video_items)
        review_ids = _review_ids(review_payload)
        failed_ids = cls._failure_ids(image_payload, video_payload, failed_payload)

        images: list[ImageCandidate] = []
        videos: list[VideoCandidate] = []
        source_paths: dict[str, Path] = {}
        raw_types: dict[str, str] = {}
        seen_images: set[str] = set()
        seen_videos: set[str] = set()
        for raw in image_items:
            candidate, stable_id = cls._coerce_image(raw)
            if candidate is None or stable_id in seen_images:
                continue
            seen_images.add(stable_id)
            images.append(candidate)
            raw_types[stable_id] = " ".join(
                [str(raw.get("image_type", "")), *[str(value) for value in raw.get("review_reasons", [])]]
            ).casefold()
            source = cls._source_path(raw.get("local_path"), root)
            if source is not None:
                source_paths[stable_id] = source
        for raw in video_items:
            candidate, stable_id = cls._coerce_video(raw)
            if candidate is None or stable_id in seen_videos:
                continue
            seen_videos.add(stable_id)
            videos.append(candidate)
            raw_types[stable_id] = str(raw.get("media_type", "")).casefold()
            source = cls._source_path(raw.get("local_path"), root)
            if source is not None:
                source_paths[stable_id] = source

        session = cls(
            root,
            task_root=managed_root,
            state_path=state,
            images=images,
            videos=videos,
            news_items=news_items,
            source_paths=source_paths,
            raw_types=raw_types,
            review_ids=review_ids,
            failed_ids=failed_ids,
        )
        session._initialize_decisions()
        return session

    @staticmethod
    def _news_items(
        image_payload: Any,
        video_payload: Any,
        image_items: list[dict[str, Any]],
        video_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        source: list[dict[str, Any]] = []
        for payload in (image_payload, video_payload):
            if isinstance(payload, dict):
                values = payload.get("news_items") or payload.get("items") or []
                if isinstance(values, list):
                    source.extend(item for item in values if isinstance(item, dict))
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in sorted(source, key=lambda value: (int(value.get("sequence", 10**9) or 10**9), str(value.get("news_id", "")))):
            news_id = str(item.get("news_id") or item.get("id") or "")
            if news_id and news_id not in seen:
                result.append(dict(item, news_id=news_id))
                seen.add(news_id)
        for raw in [*image_items, *video_items]:
            news_id = str(raw.get("news_id") or "")
            if news_id and news_id not in seen:
                result.append({"news_id": news_id, "sequence": len(result) + 1, "section": "新作", "title": news_id})
                seen.add(news_id)
        return result

    @staticmethod
    def _failure_ids(*payloads: Any) -> set[str]:
        result: set[str] = set()
        for payload in payloads:
            values = _items(payload, ("failures",)) if isinstance(payload, dict) else payload if isinstance(payload, list) else []
            for value in values:
                if isinstance(value, dict) and value.get("candidate_id") is not None:
                    result.add(str(value["candidate_id"]))
        return result

    @staticmethod
    def _source_path(value: Any, root: Path) -> Path | None:
        if not value:
            return None
        candidate = Path(str(value))
        candidates = [candidate] if candidate.is_absolute() else [root / candidate, candidate]
        for path in candidates:
            try:
                if path.is_file():
                    return path.resolve()
            except OSError:
                continue
        return None

    @classmethod
    def _coerce_image(cls, raw: dict[str, Any]) -> tuple[ImageCandidate | None, str]:
        data = dict(raw)
        image_url = str(data.get("image_url") or data.get("url") or "")
        stable_id = str(data.get("id") or (candidate_id_for(image_url) if image_url else _stable_fallback(data, "image")))
        news_id = str(data.get("news_id") or data.get("newsId") or "")
        if not news_id:
            return None, stable_id
        score_data = data.get("score") if isinstance(data.get("score"), dict) else {}
        total = _clamp(score_data.get("total", data.get("total", 0.0)), 0.0, 100.0)
        score = {
            "relevance": _clamp(score_data.get("relevance", data.get("relevance", total / 100.0)), 0.0, 1.0),
            "freshness": _clamp(score_data.get("freshness", 0.0), 0.0, 1.0),
            "source_trust": _clamp(score_data.get("source_trust", 0.0), 0.0, 1.0),
            "quality": _clamp(score_data.get("quality", data.get("quality", 0.0)), 0.0, 1.0),
            "duplicate_penalty": _clamp(score_data.get("duplicate_penalty", 0.0), 0.0, 1.0),
            "risk_penalty": _clamp(score_data.get("risk_penalty", 0.0), 0.0, 1.0),
            "type_match": _clamp(score_data.get("type_match", 0.0), 0.0, 1.0),
            "total": total,
        }
        payload = {
            "id": stable_id,
            "news_id": news_id,
            "image_url": image_url or f"legacy://{stable_id}",
            "source_url": str(data.get("source_url") or data.get("source") or f"legacy://{stable_id}"),
            "source_type": data.get("source_type", SourceType.UNKNOWN.value),
            "image_type": data.get("image_type", ImageType.UNKNOWN.value),
            "image_type_confidence": _clamp(data.get("image_type_confidence", 0.0), 0.0, 1.0),
            "published_at": _parse_datetime(data["published_at"]) if data.get("published_at") else None,
            "fetched_at": _parse_datetime(data.get("fetched_at")),
            "width": data.get("width"),
            "height": data.get("height"),
            "mime_type": data.get("mime_type"),
            "byte_size": data.get("byte_size"),
            "sha256": data.get("sha256"),
            "perceptual_hash": data.get("perceptual_hash"),
            "downloadable": bool(data.get("downloadable", False)),
            "local_path": str(data["local_path"]) if data.get("local_path") else None,
            "review_reasons": [str(item) for item in data.get("review_reasons", []) if item is not None] if isinstance(data.get("review_reasons", []), list) else [],
            "signals": data.get("signals", {}) if isinstance(data.get("signals"), dict) else {},
            "score": score,
            "selected": bool(data.get("selected", False)),
        }
        try:
            candidate = ImageCandidate.model_validate(payload)
        except (TypeError, ValueError):
            try:
                payload["width"] = int(payload["width"]) if payload["width"] is not None else None
                payload["height"] = int(payload["height"]) if payload["height"] is not None else None
                payload["byte_size"] = int(payload["byte_size"]) if payload["byte_size"] is not None else None
                candidate = ImageCandidate.model_validate(payload)
            except (TypeError, ValueError):
                return None, stable_id
        # Domain models derive IDs from URLs.  Legacy indexes may have an
        # explicit stable ID, which must remain the review identity.
        object.__setattr__(candidate, "id", stable_id)
        return candidate, stable_id

    @classmethod
    def _coerce_video(cls, raw: dict[str, Any]) -> tuple[VideoCandidate | None, str]:
        data = dict(raw)
        video_url = str(data.get("video_url") or data.get("url") or "")
        stable_id = str(data.get("id") or (video_candidate_id_for(video_url) if video_url else _stable_fallback(data, "video")))
        news_id = str(data.get("news_id") or data.get("newsId") or "")
        if not news_id:
            return None, stable_id
        payload = {
            "id": stable_id,
            "news_id": news_id,
            "source_url": str(data.get("source_url") or f"legacy://{stable_id}"),
            "video_url": video_url or f"legacy://{stable_id}",
            "title": str(data.get("title") or ""),
            "uploader": str(data.get("uploader") or ""),
            "duration_seconds": data.get("duration_seconds"),
            "width": data.get("width"),
            "height": data.get("height"),
            "format": data.get("format"),
            "mime_type": data.get("mime_type"),
            "byte_size": data.get("byte_size"),
            "sha256": data.get("sha256"),
            "local_path": str(data["local_path"]) if data.get("local_path") else None,
            "downloadable": bool(data.get("downloadable", False)),
            "review_reasons": [str(item) for item in data.get("review_reasons", []) if item is not None] if isinstance(data.get("review_reasons", []), list) else [],
            "status": data.get("status", VideoStatus.DISCOVERED.value),
            "failure_reason": data.get("failure_reason"),
            "discovered_via": data.get("discovered_via", "unknown"),
        }
        try:
            candidate = VideoCandidate.model_validate(payload)
        except (TypeError, ValueError):
            return None, stable_id
        object.__setattr__(candidate, "id", stable_id)
        return candidate, stable_id

    def _initialize_decisions(self) -> None:
        for candidate in self.images:
            self._decisions[str(candidate.id)] = self._initial_image_decision(candidate)
        for candidate in self.videos:
            self._decisions[str(candidate.id)] = self._initial_video_decision(candidate)
        persisted = self._read_state()
        for media_id, value in persisted.items():
            if media_id in self._decisions:
                try:
                    self._decisions[media_id] = ReviewDecision(value)
                except ValueError:
                    continue
        # Persist initial state so a fresh task has a durable review contract.
        self._persist()

    def _usable(self, candidate: ImageCandidate | VideoCandidate) -> bool:
        source = self._source_paths.get(str(candidate.id))
        return source is not None and source.is_file()

    def _hard_filtered(self, candidate: ImageCandidate) -> bool:
        raw_type = self._raw_types.get(str(candidate.id), "")
        image_type = getattr(candidate.image_type, "value", str(candidate.image_type)).casefold()
        raw_tokens = set(re.split(r"[\s,;|]+", raw_type))
        if raw_tokens & (self._HARD_IMAGE_TYPES | {"low_quality", "image_too_small", "pixel_count_too_small"}) or image_type in self._HARD_IMAGE_TYPES:
            return True
        signals = candidate.signals if isinstance(candidate.signals, dict) else {}
        if signals.get("low_quality") is True or signals.get("hard_filtered") is True:
            return True
        reasons = {str(getattr(value, "value", value)).casefold() for value in candidate.review_reasons}
        if reasons & {"logo", "favicon", "icon", "ui", "low_quality", "image_too_small", "pixel_count_too_small"}:
            return True
        width, height = candidate.width, candidate.height
        if width is not None and height is not None:
            if width < self._LOW_QUALITY_MIN_WIDTH or height < self._LOW_QUALITY_MIN_HEIGHT or width * height < self._LOW_QUALITY_MIN_PIXELS:
                return True
        return False

    def _initial_image_decision(self, candidate: ImageCandidate) -> ReviewDecision:
        if self._hard_filtered(candidate):
            return ReviewDecision.REJECTED
        if bool(candidate.selected) and self._usable(candidate):
            return ReviewDecision.ACCEPTED
        if self._usable(candidate):
            return ReviewDecision.PENDING
        return ReviewDecision.REJECTED

    def _initial_video_decision(self, candidate: VideoCandidate) -> ReviewDecision:
        if self._usable(candidate) and candidate.status is VideoStatus.DOWNLOADED:
            return ReviewDecision.ACCEPTED
        if self._usable(candidate):
            return ReviewDecision.PENDING
        return ReviewDecision.REJECTED

    def _read_state(self) -> dict[str, str]:
        payload = _read_json(self.state_path, {})
        if not isinstance(payload, dict):
            return {}
        values = payload.get("decisions", payload)
        return values if isinstance(values, dict) else {}

    def _persist(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.state_path.name}.", suffix=".tmp", dir=self.state_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    {
                        "schema_version": 1,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "decisions": {key: value.value for key, value in sorted(self._decisions.items())},
                    },
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
                handle.write("\n")
            os.replace(temp_name, self.state_path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def decision(self, media_id: str) -> ReviewDecision:
        return self._decisions.get(str(media_id), ReviewDecision.REJECTED)

    def set_decision(self, media_id: str, decision: ReviewDecision | str) -> None:
        media_id = str(media_id)
        if media_id not in self._decisions:
            raise KeyError(media_id)
        self._decisions[media_id] = decision if isinstance(decision, ReviewDecision) else ReviewDecision(decision)
        self._persist()

    def _sorted_pending(self, values: Iterable[ImageCandidate]) -> list[ImageCandidate]:
        def key(candidate: ImageCandidate):
            score = candidate.score
            return (
                -(score.relevance if score else 0.0),
                -(score.total if score else 0.0),
                str(candidate.id),
            )

        return sorted(values, key=key)

    def pending_images(self, news_id: str | None = None) -> list[ImageCandidate]:
        values = [candidate for candidate in self.images if self.decision(str(candidate.id)) is ReviewDecision.PENDING]
        if news_id is not None:
            values = [candidate for candidate in values if candidate.news_id == news_id]
        return self._sorted_pending(values)

    def failed_images(self, news_id: str | None = None) -> list[ImageCandidate]:
        values = [candidate for candidate in self.pending_images(news_id) if str(candidate.id) in self._failed_ids or bool(candidate.review_reasons)]
        return values

    def accepted_images(self, news_id: str | None = None) -> list[ImageCandidate]:
        values = [candidate for candidate in self.images if self.decision(str(candidate.id)) is ReviewDecision.ACCEPTED]
        return [candidate for candidate in values if news_id is None or candidate.news_id == news_id]

    def rejected_images(self, news_id: str | None = None) -> list[ImageCandidate]:
        values = [candidate for candidate in self.images if self.decision(str(candidate.id)) is ReviewDecision.REJECTED]
        return [candidate for candidate in values if news_id is None or candidate.news_id == news_id]

    def pending_videos(self, news_id: str | None = None) -> list[VideoCandidate]:
        values = [candidate for candidate in self.videos if self.decision(str(candidate.id)) is ReviewDecision.PENDING]
        return [candidate for candidate in values if news_id is None or candidate.news_id == news_id]

    def accepted_videos(self, news_id: str | None = None) -> list[VideoCandidate]:
        values = [candidate for candidate in self.videos if self.decision(str(candidate.id)) is ReviewDecision.ACCEPTED]
        return [candidate for candidate in values if news_id is None or candidate.news_id == news_id]

    def _confined_retry_path(self, news_id: str, attempt_id: str) -> Path:
        """Resolve a retry path while rejecting traversal and symlink escapes."""

        if self.task_root is None:
            raise ValueError("retry merge requires a task_root")
        components = (str(news_id), str(attempt_id))
        for component in components:
            value = Path(component)
            if not component or value.is_absolute() or value.name != component or component in {".", ".."}:
                raise ValueError("retry path must be a single confined component")
        retries_root = (Path(self.task_root) / "retries").resolve()
        lexical = Path(self.task_root) / "retries" / components[0] / components[1]
        resolved = lexical.resolve()
        try:
            resolved.relative_to(retries_root)
        except ValueError as exc:
            raise ValueError("retry path escapes task_root/retries") from exc
        return resolved

    def merge_retry_attempt(self, retry_or_news_id: Any, attempt_id: str | None = None) -> RetryMergeResult:
        """Merge one retry attempt in memory while retaining raw indexes.

        ``retry_or_news_id`` may be an attempt directory, a ``RetryAttempt``
        returned by ``TaskStore``, or a news ID paired with ``attempt_id``.
        Existing stable IDs always win; only new records are appended.
        """

        retry_data = None
        if hasattr(retry_or_news_id, "images") and hasattr(retry_or_news_id, "videos"):
            retry_data = retry_or_news_id
            news_id = str(retry_data.news_id)
            resolved_attempt_id = str(retry_data.attempt_id)
            root = self._confined_retry_path(news_id, resolved_attempt_id)
            if Path(retry_data.path).resolve() != root:
                raise ValueError("retry path is outside task_root/retries")
            image_items = retry_data.images
            video_items = retry_data.videos
        else:
            if attempt_id is not None:
                news_id = str(retry_or_news_id)
                resolved_attempt_id = str(attempt_id)
                root = self._confined_retry_path(news_id, resolved_attempt_id)
            else:
                raw_path = Path(retry_or_news_id).expanduser()
                if raw_path.is_absolute():
                    raise ValueError("retry path must be relative to task_root/retries")
                root = raw_path.resolve()
                retries_root = (Path(self.task_root) / "retries").resolve()
                try:
                    root.relative_to(retries_root)
                except ValueError as exc:
                    raise ValueError("retry path escapes task_root/retries") from exc
                resolved_attempt_id = root.name
                news_id = root.parent.name
            if not root.is_dir():
                raise FileNotFoundError(root)
            image_items = _items(_read_json(root / "image_index.json", {}), ("candidates", "images", "image_candidates"))
            video_items = _items(_read_json(root / "video_index.json", {}), ("videos", "video_candidates", "candidates"))

        added_images: list[str] = []
        added_videos: list[str] = []
        for raw in image_items:
            candidate, stable_id = self._coerce_image(raw)
            if candidate is None or stable_id in self._image_by_id:
                continue
            self.images.append(candidate)
            self._image_by_id[stable_id] = candidate
            self._raw_types[stable_id] = str(raw.get("image_type", "")).casefold()
            source = self._source_path(raw.get("local_path"), root)
            if source is not None:
                self._source_paths[stable_id] = source
            self._review_ids.add(stable_id)
            self._decisions[stable_id] = self._initial_image_decision(candidate)
            added_images.append(stable_id)
        for raw in video_items:
            candidate, stable_id = self._coerce_video(raw)
            if candidate is None or stable_id in self._video_by_id:
                continue
            self.videos.append(candidate)
            self._video_by_id[stable_id] = candidate
            source = self._source_path(raw.get("local_path"), root)
            if source is not None:
                self._source_paths[stable_id] = source
            self._decisions[stable_id] = self._initial_video_decision(candidate)
            added_videos.append(stable_id)
        if added_images or added_videos:
            self._persist()
        return RetryMergeResult(news_id, resolved_attempt_id, added_images, added_videos)

    def _folder_names(self) -> dict[str, str]:
        # Use the same canonical naming policy as raw output.  Manifest order
        # is the editorial appearance order; sorting by global sequence would
        # renumber categories when legacy manifests contain non-monotonic IDs.
        names = OutputManager.item_names(self.news_items)
        counters = {
            prefix: sum(name.startswith(prefix) for name in names.values())
            for prefix in ("x", "h", "z")
        }
        for candidate in [*self.images, *self.videos]:
            if str(candidate.news_id) not in names:
                # A media record without a corresponding news row belongs to
                # the catch-all weekly namespace, never implicitly to 新作.
                counters["z"] += 1
                names[str(candidate.news_id)] = f"z{counters['z']}"
        return names

    def _source_for(self, candidate: ImageCandidate | VideoCandidate) -> Path | None:
        source = self._source_paths.get(str(candidate.id))
        if source is not None and source.is_file():
            return source
        local = getattr(candidate, "local_path", None)
        return self._source_path(local, self.output_dir)

    @staticmethod
    def _unique_target(target: Path, source: Path) -> Path:
        if not target.exists() or target.resolve() == source.resolve():
            return target
        stem, suffix = target.stem, target.suffix
        index = 2
        while True:
            candidate = target.with_name(f"{stem} ({index}){suffix}")
            if not candidate.exists() or candidate.resolve() == source.resolve():
                return candidate
            index += 1

    def export_final(self) -> dict[str, Any]:
        root = self.task_root
        final_root = root / "final"
        image_root = final_root / "images"
        image_root.mkdir(parents=True, exist_ok=True)
        folder_names = self._folder_names()
        files: list[str] = []
        image_ranks: dict[str, int] = {}
        for candidate in self.images:
            if self.decision(str(candidate.id)) is not ReviewDecision.ACCEPTED:
                continue
            source = self._source_for(candidate)
            if source is None or not source.is_file():
                continue
            folder = folder_names.get(str(candidate.news_id), f"x{candidate.news_id}")
            image_ranks[folder] = image_ranks.get(folder, 0) + 1
            extension = self._media_extension(candidate, source, default=".jpg")
            if extension == ".jpeg":
                extension = ".jpg"
            target = image_root / folder / f"{folder}.{image_ranks[folder]:02d}{extension}"
            target.parent.mkdir(parents=True, exist_ok=True)
            target = self._unique_target(target, source)
            if target.resolve() != source.resolve():
                shutil.copyfile(source, target)
            files.append(target.relative_to(final_root).as_posix())

        reserved: dict[str, set[str]] = {}
        for candidate in self.videos:
            if self.decision(str(candidate.id)) is not ReviewDecision.ACCEPTED:
                continue
            source = self._source_for(candidate)
            if source is None or not source.is_file():
                continue
            folder = folder_names.get(str(candidate.news_id), f"x{candidate.news_id}")
            target_dir = image_root / folder
            target_dir.mkdir(parents=True, exist_ok=True)
            extension = source.suffix.casefold() or ".mp4"
            name = _safe_title(candidate.title, str(candidate.id)) + extension
            used = reserved.setdefault(folder, set())
            stem, suffix = Path(name).stem, Path(name).suffix
            index = 2
            while name.casefold() in used or (target_dir / name).exists():
                name = f"{stem} ({index}){suffix}"
                index += 1
            used.add(name.casefold())
            target = target_dir / name
            if target.resolve() != source.resolve():
                shutil.copyfile(source, target)
            files.append(target.relative_to(final_root).as_posix())

        manifest = {
            "schema_version": 1,
            "issue_id": self._issue_id(),
            "files": files,
            "image_count": sum(1 for value in files if not value.casefold().endswith((".mp4", ".webm", ".mov", ".mkv", ".avi"))),
            "video_count": sum(1 for value in files if value.casefold().endswith((".mp4", ".webm", ".mov", ".mkv", ".avi"))),
        }
        self._atomic_json(final_root / "final_manifest.json", manifest)
        return manifest

    def _issue_id(self) -> str:
        for candidate in [self.output_dir / "image_index.json", self.output_dir / "video_index.json"]:
            payload = _read_json(candidate, {})
            if isinstance(payload, dict) and payload.get("issue_id") is not None:
                return str(payload["issue_id"])
        return ""

    @staticmethod
    def _media_extension(candidate: ImageCandidate | VideoCandidate, source: Path, *, default: str) -> str:
        mime_type = getattr(candidate, "mime_type", None)
        if isinstance(mime_type, str) and "/" in mime_type:
            suffix = mime_type.split("/", 1)[1].split(";", 1)[0].strip().casefold()
            if suffix == "jpeg":
                suffix = "jpg"
            if suffix and re.fullmatch(r"[a-z0-9]+", suffix):
                return f".{suffix}"
        return source.suffix.casefold() or default

    def export(self) -> dict[str, Any]:
        return self.export_final()

    @classmethod
    def load(cls, output_dir: Path | str, **kwargs: Any) -> "ReviewSession":
        return cls.from_output(output_dir, **kwargs)

    @staticmethod
    def _atomic_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
