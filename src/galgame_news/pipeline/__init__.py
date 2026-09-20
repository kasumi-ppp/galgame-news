"""Reusable pipeline orchestration for CLI and desktop frontends."""

from .contracts import (
    CancellationRequested,
    CancellationToken,
    Checkpoint,
    EventSink,
    ProgressEvent,
    ProgressEventSink,
    TaskRequest,
    TaskResult,
)
from .runner import PipelineRunner

__all__ = [
    "CancellationRequested",
    "CancellationToken",
    "Checkpoint",
    "EventSink",
    "PipelineRunner",
    "ProgressEvent",
    "ProgressEventSink",
    "TaskRequest",
    "TaskResult",
]
