"""Stable orchestration contracts shared by the CLI and desktop frontends."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Condition, Event
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..domain import (
    FailureRecord,
    ImageCandidate,
    Issue,
    PipelineResult,
    SourceRef,
    VideoCandidate,
)


class CancellationRequested(RuntimeError):
    """Raised internally when cooperative task cancellation was requested."""


class CancellationToken:
    """Small thread-safe cancellation primitive.

    Pipeline stages check this token between network operations and after each
    news item.  It intentionally does not forcefully terminate a worker or an
    in-flight third-party request.
    """

    def __init__(self) -> None:
        self._event = Event()
        self._pause_condition = Condition()
        self._paused = False

    def cancel(self) -> None:
        # Wake a worker that is waiting in ``wait_if_paused``.  Cancellation
        # remains cooperative: an in-flight request is allowed to finish and
        # the runner observes this flag at its next safe point.
        self._event.set()
        with self._pause_condition:
            self._pause_condition.notify_all()

    def pause(self) -> None:
        """Pause at the next pipeline safe point without ending the worker."""

        with self._pause_condition:
            if not self._event.is_set():
                self._paused = True

    def resume(self) -> None:
        """Release a paused worker and let it continue from its current point."""

        with self._pause_condition:
            self._paused = False
            self._pause_condition.notify_all()

    @property
    def is_paused(self) -> bool:
        with self._pause_condition:
            return self._paused

    def wait_if_paused(self) -> None:
        """Block while paused, while still allowing cancellation to wake us."""

        with self._pause_condition:
            while self._paused and not self._event.is_set():
                self._pause_condition.wait()
        self.raise_if_cancelled()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled:
            raise CancellationRequested("pipeline cancellation requested")


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """A serializable progress notification; no UI concerns are embedded."""

    kind: str
    task_id: str
    issue_id: str
    news_id: str | None = None
    news_index: int | None = None
    total_news: int | None = None
    message: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def stage(self) -> str:
        """Compatibility alias for consumers that call the event stage."""
        return self.kind


class ProgressEventSink(Protocol):
    def __call__(self, event: ProgressEvent) -> None: ...


class Checkpoint(BaseModel):
    """Strict, versioned on-disk state for one pipeline task."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = Field(default=1, frozen=True)
    status: Literal["running", "cancelled", "completed", "failed"]
    task_id: str = Field(min_length=1)
    issue_id: str = Field(min_length=1)
    input_sha256: str = Field(min_length=1)
    config_sha256: str = Field(min_length=1)
    issue: Issue
    completed_news_ids: list[str] = Field(default_factory=list)
    candidates: list[ImageCandidate] = Field(default_factory=list)
    videos: list[VideoCandidate] = Field(default_factory=list)
    failures: list[FailureRecord] = Field(default_factory=list)
    source_map: dict[str, list[SourceRef]] = Field(default_factory=dict)
    result: PipelineResult | None = None


@dataclass(slots=True)
class TaskRequest:
    """Input for one pipeline run.

    Normal runner calls create an isolated ``raw`` directory beneath
    ``output_dir``.  ``legacy_output`` is used only by the compatibility
    ``Application.run`` wrapper so existing CLI output paths remain unchanged.
    """

    input_path: Path | str
    issue_id: str
    output_dir: Path | str
    offline: bool = False
    no_videos: bool = False
    config_path: Path | str | None = None
    history_db: Path | str | None = None
    max_images: int | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    resume: bool = False
    task_dir: Path | str | None = None
    checkpoint_path: Path | str | None = None
    task_id: str | None = None

    def __post_init__(self) -> None:
        self.input_path = Path(self.input_path)
        self.output_dir = Path(self.output_dir)
        if self.config_path is not None:
            self.config_path = Path(self.config_path)
        if self.history_db is not None:
            self.history_db = Path(self.history_db)
        if self.task_dir is not None:
            self.task_dir = Path(self.task_dir)
        if self.checkpoint_path is not None:
            self.checkpoint_path = Path(self.checkpoint_path)
        if not self.issue_id.strip():
            raise ValueError("issue_id must not be empty")


@dataclass(slots=True)
class TaskResult:
    """Result envelope returned by :class:`PipelineRunner`."""

    status: str
    task_id: str
    task_dir: Path
    output_dir: Path
    checkpoint_path: Path
    pipeline_result: PipelineResult
    resumed: bool = False

    @property
    def result(self) -> PipelineResult:
        """Short alias for callers that use ``result.result``."""
        return self.pipeline_result

    @property
    def issue(self):
        return self.pipeline_result.issue

    @property
    def candidates(self):
        return self.pipeline_result.candidates

    @property
    def failures(self):
        return self.pipeline_result.failures

    @property
    def videos(self):
        return self.pipeline_result.videos


EventSink = Callable[[ProgressEvent], None]
