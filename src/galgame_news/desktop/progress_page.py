"""Progress page driven only by structured ``ProgressEvent`` objects."""

from __future__ import annotations

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ProgressPage(QWidget):
    stop_requested = Signal()
    cancel_requested = Signal()
    pause_requested = Signal()
    resume_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("progressPage")
        self.counters: dict[str, int] = {"completed": 0, "total": 0, "failed": 0}
        self.status_label = QLabel("Idle")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(False)
        self.completed_label = QLabel("0")
        self.total_label = QLabel("0")
        self.failed_label = QLabel("0")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.pause_button = QPushButton("Pause")
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(self.request_pause_or_resume)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.request_stop)

        counters = QGroupBox("Counters")
        form = QFormLayout(counters)
        form.addRow("Completed", self.completed_label)
        form.addRow("Total", self.total_label)
        form.addRow("Failed", self.failed_label)
        layout = QVBoxLayout(self)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(counters)
        layout.addWidget(self.log)
        layout.addWidget(self.pause_button)
        layout.addWidget(self.stop_button)

    def set_running(self, running: bool) -> None:
        self.stop_button.setEnabled(bool(running))
        self.pause_button.setEnabled(bool(running))
        self.progress_bar.setVisible(bool(running))
        if running:
            self.pause_button.setText("Pause")
            self.status_label.setText("Running")
        else:
            self.pause_button.setText("Pause")

    def set_paused(self, paused: bool) -> None:
        """Reflect the cooperative token state without stopping the worker."""

        paused = bool(paused)
        self.pause_button.setText("Resume" if paused else "Pause")
        if paused:
            self.status_label.setText("Paused")
        elif self.pause_button.isEnabled():
            self.status_label.setText("Running")

    @Slot()
    def request_pause_or_resume(self) -> None:
        if self.pause_button.text() == "Resume":
            self.resume_requested.emit()
        else:
            self.pause_requested.emit()

    @Slot()
    def request_stop(self) -> None:
        self.stop_requested.emit()
        self.cancel_requested.emit()

    @Slot(object)
    def handle_event(self, event: object) -> None:
        kind = str(getattr(event, "kind", "event"))
        payload = getattr(event, "payload", {}) or {}
        message = str(getattr(event, "message", "") or kind)
        if kind in {"news_completed", "news_finished"}:
            self.counters["completed"] += 1
        if kind in {"candidate_failed", "news_failed", "task_failed"}:
            self.counters["failed"] += 1
        total = getattr(event, "total_news", None)
        if total is None:
            total = payload.get("total_news", payload.get("total"))
        if total is not None:
            try:
                self.counters["total"] = max(self.counters["total"], int(total))
            except (TypeError, ValueError):
                pass
        completed = payload.get("completed")
        if completed is not None:
            try:
                self.counters["completed"] = max(self.counters["completed"], int(completed))
            except (TypeError, ValueError):
                pass
        self.completed_label.setText(str(self.counters["completed"]))
        self.total_label.setText(str(self.counters["total"]))
        self.failed_label.setText(str(self.counters["failed"]))
        if self.counters["total"]:
            self.progress_bar.setRange(0, self.counters["total"])
            self.progress_bar.setValue(self.counters["completed"])
        self.log.appendPlainText(message)

    def report_failure(self, message: str, *, count: bool = True) -> None:
        """Show a failure that happened outside the pipeline event stream.

        Runner construction can fail before a worker exists, so there is no
        structured event to deliver through ``handle_event``.  Keep that
        failure visible in the same log and counters used by pipeline events.
        """

        if count:
            self.counters["failed"] += 1
        self.failed_label.setText(str(self.counters["failed"]))
        self.log.appendPlainText(f"Failed: {message}")

    def reset(self) -> None:
        self.counters = {"completed": 0, "total": 0, "failed": 0}
        self.completed_label.setText("0")
        self.total_label.setText("0")
        self.failed_label.setText("0")
        self.log.clear()
        self.status_label.setText("Idle")
        self.pause_button.setText("Pause")

    def set_finished(self, status: str = "Completed") -> None:
        self.set_running(False)
        self.status_label.setText(str(status))


__all__ = ["ProgressPage"]
