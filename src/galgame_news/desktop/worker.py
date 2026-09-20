"""Qt worker boundary for one cooperative pipeline run."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from ..pipeline import CancellationRequested, CancellationToken, ProgressEvent


class PipelineWorker(QObject):
    """Run ``PipelineRunner`` on a ``QThread`` without parsing stdout."""

    event = Signal(object)
    progress = Signal(object)
    finished = Signal(object)
    result = Signal(object)
    cancelled = Signal(object)
    failed = Signal(object)
    error = Signal(object)

    def __init__(self, runner: Any, request: Any, token: CancellationToken | None = None):
        super().__init__()
        self.runner = runner
        self.request = request
        self.token = token or CancellationToken()

    def cancel(self) -> None:
        """Request cancellation at the next pipeline checkpoint."""

        self.token.cancel()

    def pause(self) -> None:
        """Pause the pipeline at its next cooperative safe point."""

        self.token.pause()

    def resume(self) -> None:
        """Resume a pipeline paused at a cooperative safe point."""

        self.token.resume()

    def _emit_event(self, value: ProgressEvent) -> None:
        if not isinstance(value, ProgressEvent):
            return
        self.event.emit(value)
        self.progress.emit(value)

    @Slot()
    def run(self) -> None:
        try:
            value = self.runner.run(
                self.request,
                event_sink=self._emit_event,
                cancellation_token=self.token,
            )
        except CancellationRequested as exc:
            self.cancelled.emit(exc)
        except BaseException as exc:  # worker boundary must report all failures
            self.failed.emit(exc)
            self.error.emit(exc)
        else:
            status = str(getattr(value, "status", "")).casefold()
            if self.token.is_cancelled or status == "cancelled":
                self.cancelled.emit(value)
            elif status == "failed":
                self.failed.emit(value)
            else:
                self.finished.emit(value)
                self.result.emit(value)


TaskWorker = PipelineWorker

__all__ = ["PipelineWorker", "TaskWorker"]
