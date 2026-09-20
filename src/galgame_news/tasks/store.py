"""Durable task catalog and non-destructive task-directory helpers.

The desktop toolbox keeps its catalog separate from pipeline output.  A task
directory is therefore safe to remove from the catalog without removing any
user media, and imported legacy output is only ever read by this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_TASK_SCHEMA_VERSION = 1
_SAFE_COMPONENT = re.compile(r"[^0-9A-Za-z._-]+")


def _safe_component(value: str, fallback: str) -> str:
    cleaned = _SAFE_COMPONENT.sub("_", value.strip()).strip("._")
    return cleaned or fallback


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return default
    return value


@dataclass(frozen=True)
class TaskRecord:
    """A catalog row for one task.

    Paths are serialized as strings deliberately: this keeps the record stable
    across process boundaries and mirrors the data consumed by the desktop UI.
    """

    task_id: str
    issue_id: str
    task_root: str
    input_path: str | None = None
    source_output: str | None = None
    input_sha256: str | None = None
    state_path: str | None = None
    imported: bool = False
    created_at: str = ""
    active: bool = True

    @property
    def root_path(self) -> Path:
        return Path(self.task_root)

    @property
    def id(self) -> str:
        return self.task_id

    @property
    def path(self) -> Path:
        return self.root_path

    @property
    def review_state_path(self) -> Path:
        return self.root_path / "review_state.json"

    @property
    def source_output_path(self) -> Path | None:
        return Path(self.source_output) if self.source_output else None


@dataclass
class RetryAttempt:
    """Raw retry data read from one attempt directory.

    This object intentionally contains plain dictionaries.  It is a data
    seam between retry storage and review code and never calls PipelineRunner.
    """

    task_id: str
    news_id: str
    attempt_id: str
    path: Path
    images: list[dict[str, Any]]
    videos: list[dict[str, Any]]
    failures: list[dict[str, Any]]

    @property
    def candidates(self) -> list[dict[str, Any]]:
        return self.images


class TaskStore:
    """Catalog one active task and its historical task records.

    ``app_data`` is injectable for tests and portable installations.  Without
    it, Windows uses ``%LOCALAPPDATA%/GalgameNewsToolbox`` as the persistent
    root.
    """

    def __init__(self, app_data: Path | str | None = None):
        if app_data is None:
            local_app_data = os.environ.get("LOCALAPPDATA")
            base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
            app_data = base / "GalgameNewsToolbox"
        self.root = Path(app_data).expanduser()
        self.app_data = self.root
        self.tasks_root = self.root / "tasks"
        self.root.mkdir(parents=True, exist_ok=True)
        self.tasks_root.mkdir(parents=True, exist_ok=True)
        self.catalog_path = self.root / "task_catalog.sqlite3"
        self._connection = sqlite3.connect(self.catalog_path)
        self._connection.row_factory = sqlite3.Row
        self._initialize_catalog()

    def _initialize_catalog(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                issue_id TEXT NOT NULL,
                task_root TEXT NOT NULL,
                input_path TEXT,
                source_output TEXT,
                input_sha256 TEXT,
                state_path TEXT,
                imported INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_active_created
                ON tasks(active, created_at DESC);
            """
        )
        self._connection.commit()

    @staticmethod
    def _materialize_task_root(task_root: Path) -> None:
        for name in ("raw", "retries", "final", "logs", ".work"):
            (task_root / name).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _initialize_task_state(path: Path, record: TaskRecord) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS task_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS retry_attempts (
                    news_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(news_id, attempt_id)
                );
                CREATE TABLE IF NOT EXISTS review_decisions (
                    media_id TEXT PRIMARY KEY,
                    decision TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            values = {
                "schema_version": str(_TASK_SCHEMA_VERSION),
                "task_id": record.task_id,
                "issue_id": record.issue_id,
                "imported": "1" if record.imported else "0",
            }
            connection.executemany(
                "INSERT INTO task_meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                values.items(),
            )
            connection.commit()
        finally:
            connection.close()

    def _default_task_root(self, issue_id: str, task_id: str) -> Path:
        label = _safe_component(issue_id, "task")
        return self.tasks_root / f"{label}-{task_id}"

    def _record(
        self,
        *,
        task_id: str | None = None,
        issue_id: str,
        input_path: Path | None,
        source_output: Path | None,
        task_root: Path,
        imported: bool,
    ) -> TaskRecord:
        task_id = task_id or uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        state_path = task_root / "task_state.sqlite3"
        record = TaskRecord(
            task_id=task_id,
            issue_id=str(issue_id),
            task_root=str(task_root),
            input_path=str(input_path) if input_path else None,
            source_output=str(source_output) if source_output else None,
            input_sha256=_sha256(input_path) if input_path else None,
            state_path=str(state_path),
            imported=imported,
            created_at=created_at,
            active=True,
        )
        self._materialize_task_root(task_root)
        self._initialize_task_state(state_path, record)
        self._connection.execute("UPDATE tasks SET active = 0 WHERE active = 1")
        self._connection.execute(
            """INSERT INTO tasks(
                task_id, issue_id, task_root, input_path, source_output,
                input_sha256, state_path, imported, created_at, active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                record.task_id,
                record.issue_id,
                record.task_root,
                record.input_path,
                record.source_output,
                record.input_sha256,
                record.state_path,
                int(record.imported),
                record.created_at,
            ),
        )
        self._connection.commit()
        return record

    def create_task(self, issue_id: str, input_path: Path | str, *, task_root: Path | str | None = None) -> TaskRecord:
        """Create and catalog a managed task directory."""

        source = Path(input_path).expanduser()
        task_id = uuid.uuid4().hex
        root = Path(task_root).expanduser() if task_root is not None else self._default_task_root(issue_id, task_id)
        # ``_record`` creates its own catalog id; reserve the generated root
        # with a fresh id only when no caller-supplied root was given.
        return self._record(
            task_id=task_id,
            issue_id=issue_id,
            input_path=source,
            source_output=None,
            task_root=root,
            imported=False,
        )

    def import_output(
        self,
        output_dir: Path | str,
        *,
        issue_id: str | None = None,
        task_root: Path | str | None = None,
    ) -> TaskRecord:
        """Catalog an existing output directory without writing into it."""

        source = Path(output_dir).expanduser()
        if not source.is_dir():
            raise FileNotFoundError(source)
        inferred_issue = issue_id or self._infer_issue_id(source) or source.name or "imported"
        task_id = uuid.uuid4().hex
        if task_root is not None:
            root = Path(task_root).expanduser()
            source_resolved = source.resolve()
            root_resolved = root.resolve()
            try:
                root_resolved.relative_to(source_resolved)
            except ValueError:
                pass
            else:
                raise ValueError("task_root must be outside legacy source output")
        else:
            root = self._default_task_root(inferred_issue, task_id)
        return self._record(
            task_id=task_id,
            issue_id=inferred_issue,
            input_path=None,
            source_output=source.resolve(),
            task_root=root,
            imported=True,
        )

    @staticmethod
    def _infer_issue_id(source: Path) -> str | None:
        for filename in ("image_index.json", "video_index.json"):
            payload = _read_json(source / filename, {})
            if isinstance(payload, dict) and payload.get("issue_id"):
                return str(payload["issue_id"])
        return None

    @staticmethod
    def _row_to_record(row: sqlite3.Row | None) -> TaskRecord | None:
        if row is None:
            return None
        return TaskRecord(
            task_id=row["task_id"],
            issue_id=row["issue_id"],
            task_root=row["task_root"],
            input_path=row["input_path"],
            source_output=row["source_output"],
            input_sha256=row["input_sha256"],
            state_path=row["state_path"],
            imported=bool(row["imported"]),
            created_at=row["created_at"],
            active=bool(row["active"]),
        )

    def get_task(self, task_id: str) -> TaskRecord | None:
        row = self._connection.execute("SELECT * FROM tasks WHERE task_id = ?", (str(task_id),)).fetchone()
        return self._row_to_record(row)

    def list_tasks(self) -> list[TaskRecord]:
        rows = self._connection.execute("SELECT * FROM tasks ORDER BY active DESC, created_at DESC, task_id DESC").fetchall()
        return [record for row in rows if (record := self._row_to_record(row)) is not None]

    @property
    def tasks(self) -> list[TaskRecord]:
        return self.list_tasks()

    @property
    def active_task(self) -> TaskRecord | None:
        row = self._connection.execute("SELECT * FROM tasks WHERE active = 1 ORDER BY created_at DESC LIMIT 1").fetchone()
        return self._row_to_record(row)

    @property
    def active(self) -> TaskRecord | None:
        return self.active_task

    def list_history(self) -> list[TaskRecord]:
        rows = self._connection.execute("SELECT * FROM tasks WHERE active = 0 ORDER BY created_at DESC, task_id DESC").fetchall()
        return [record for row in rows if (record := self._row_to_record(row)) is not None]

    def history(self) -> list[TaskRecord]:
        return self.list_history()

    def set_active(self, task_id: str) -> TaskRecord | None:
        if self.get_task(task_id) is None:
            return None
        self._connection.execute("UPDATE tasks SET active = 0")
        self._connection.execute("UPDATE tasks SET active = 1 WHERE task_id = ?", (str(task_id),))
        self._connection.commit()
        return self.get_task(task_id)

    def update_task_root(self, task: TaskRecord | str, task_root: Path | str) -> TaskRecord | None:
        """Bind a catalog row to the pipeline's per-run directory.

        A desktop task is catalogued before the worker starts so it is visible
        while running.  The pipeline chooses the final timestamped directory;
        this method records that directory once the worker returns without
        touching any pipeline output files.
        """

        record = task if isinstance(task, TaskRecord) else self.get_task(str(task))
        if record is None:
            return None
        root = Path(task_root).expanduser()
        if not root.is_absolute():
            root = root.resolve()
        if root.resolve() == record.root_path.resolve():
            return record
        bound = TaskRecord(
            task_id=record.task_id,
            issue_id=record.issue_id,
            task_root=str(root),
            input_path=record.input_path,
            source_output=record.source_output,
            input_sha256=record.input_sha256,
            state_path=str(root / "task_state.sqlite3"),
            imported=record.imported,
            created_at=record.created_at,
            active=record.active,
        )
        self._materialize_task_root(root)
        self._initialize_task_state(Path(bound.state_path), bound)
        self._connection.execute(
            "UPDATE tasks SET task_root = ?, state_path = ? WHERE task_id = ?",
            (bound.task_root, bound.state_path, bound.task_id),
        )
        self._connection.commit()
        return self.get_task(bound.task_id)

    def save_request_options(self, task: TaskRecord | str, options: dict[str, Any]) -> None:
        """Persist the non-secret run options needed for an exact resume."""

        record = task if isinstance(task, TaskRecord) else self.get_task(str(task))
        if record is None:
            raise ValueError("unknown task")
        path = record.root_path / "request_options.json"
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(options, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def load_request_options(self, task: TaskRecord | str) -> dict[str, Any]:
        """Return saved non-secret run options, or an empty mapping."""

        record = task if isinstance(task, TaskRecord) else self.get_task(str(task))
        if record is None:
            return {}
        value = _read_json(record.root_path / "request_options.json", {})
        return value if isinstance(value, dict) else {}

    def remove_from_history(self, task_id: str) -> bool:
        """Forget a catalog row while leaving its task directory untouched."""

        cursor = self._connection.execute("DELETE FROM tasks WHERE task_id = ?", (str(task_id),))
        self._connection.commit()
        return cursor.rowcount > 0

    def remove_task(self, task_id: str) -> bool:
        return self.remove_from_history(task_id)

    def remove(self, task_id: str) -> bool:
        return self.remove_from_history(task_id)

    @staticmethod
    def _confined(path: Path, root: Path) -> Path:
        resolved_root = root.resolve()
        resolved_path = path.resolve()
        try:
            resolved_path.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError("retry path escapes task retries directory") from exc
        return resolved_path

    def merge_retry_attempt(self, task: TaskRecord | str, news_id: str, attempt_id: str) -> RetryAttempt:
        """Read one retry attempt as data without mutating raw output.

        The caller can hand the returned plain records to ``ReviewSession``;
        this seam intentionally has no PipelineRunner dependency.
        """

        record = task if isinstance(task, TaskRecord) else self.get_task(str(task))
        if record is None:
            raise KeyError(f"unknown task: {task}")
        retries = Path(record.task_root) / "retries"
        attempt = self._confined(retries / str(news_id) / str(attempt_id), retries)
        if not attempt.is_dir():
            raise FileNotFoundError(attempt)
        image_payload = _read_json(attempt / "image_index.json", {})
        video_payload = _read_json(attempt / "video_index.json", {})
        images = self._items(image_payload, ("candidates", "images", "image_candidates"))
        videos = self._items(video_payload, ("videos", "video_candidates", "candidates"))
        failures = self._items(image_payload, ("failures",)) + self._items(video_payload, ("failures",))
        retry = RetryAttempt(
            task_id=record.task_id,
            news_id=str(news_id),
            attempt_id=str(attempt_id),
            path=attempt,
            images=images,
            videos=videos,
            failures=failures,
        )
        state_path = Path(record.state_path or Path(record.task_root) / "task_state.sqlite3")
        connection = sqlite3.connect(state_path)
        try:
            connection.execute(
                "INSERT OR REPLACE INTO retry_attempts(news_id, attempt_id, path, created_at) VALUES (?, ?, ?, ?)",
                (retry.news_id, retry.attempt_id, str(attempt), datetime.now(timezone.utc).isoformat()),
            )
            connection.commit()
        finally:
            connection.close()
        return retry

    def read_retry_attempt(self, task: TaskRecord | str, news_id: str, attempt_id: str) -> RetryAttempt:
        return self.merge_retry_attempt(task, news_id, attempt_id)

    @staticmethod
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

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "TaskStore":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
