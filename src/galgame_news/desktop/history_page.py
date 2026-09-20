"""Non-destructive task history page."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QPushButton, QVBoxLayout, QWidget

from ..tasks import TaskRecord, TaskStore


class HistoryPage(QWidget):
    resume_requested = Signal(object)
    review_requested = Signal(object)
    open_requested = Signal(object)

    def __init__(self, store: TaskStore | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("historyPage")
        self.store = store or TaskStore()
        self.records: list[TaskRecord] = []
        self.task_list = QListWidget()
        self.task_list.setObjectName("historyList")
        self.resume_button = QPushButton("Resume")
        self.review_button = QPushButton("Review")
        self.open_button = QPushButton("Open folder")
        self.remove_button = QPushButton("Remove from history")
        self.resume_button.clicked.connect(self.resume_selected)
        self.review_button.clicked.connect(self.review_selected)
        self.open_button.clicked.connect(self.open_selected)
        self.remove_button.clicked.connect(self.remove_selected)
        actions = QHBoxLayout()
        for button in (self.resume_button, self.review_button, self.open_button, self.remove_button):
            actions.addWidget(button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.task_list)
        layout.addLayout(actions)
        self.refresh()

    def refresh(self) -> None:
        self.records = list(self.store.list_tasks())
        self.task_list.clear()
        for record in self.records:
            label = f"{record.issue_id}  ·  {record.task_id}"
            if record.imported:
                label += "  (imported)"
            item = self.task_list.addItem(label)
            _ = item
            self.task_list.item(self.task_list.count() - 1).setData(256, record.task_id)

    def selected_record(self) -> TaskRecord | None:
        row = self.task_list.currentRow()
        return self.records[row] if 0 <= row < len(self.records) else None

    def resume_selected(self) -> None:
        record = self.selected_record()
        if record:
            self.resume_requested.emit(record)

    def review_selected(self) -> None:
        record = self.selected_record()
        if record:
            self.review_requested.emit(record)

    def open_selected(self) -> None:
        record = self.selected_record()
        if not record:
            return
        self.open_requested.emit(record)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(record.root_path)))

    def remove_selected(self) -> bool:
        record = self.selected_record()
        if not record:
            return False
        removed = bool(self.store.remove_from_history(record.task_id))
        if removed:
            self.refresh()
        return removed


__all__ = ["HistoryPage"]
