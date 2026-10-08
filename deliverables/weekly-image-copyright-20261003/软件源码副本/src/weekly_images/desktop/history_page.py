"""Non-destructive task history page."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QListWidget, QPushButton, QVBoxLayout, QWidget, QLineEdit, QLabel

from ..tasks import TaskRecord, TaskStore
from .ui import page_header


class HistoryPage(QWidget):
    resume_requested = Signal(object)
    review_requested = Signal(object)
    open_requested = Signal(object)

    def __init__(self, store: TaskStore | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("historyPage")
        self.store = store or TaskStore()
        self.records: list[TaskRecord] = []
        self._visible_records: list[TaskRecord] = []
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("按期号搜索历史任务")
        self.search_edit.textChanged.connect(self._filter)
        self.empty_label = QLabel("暂无历史任务，可在新建任务页导入已有结果。")
        self.empty_label.setObjectName("muted")
        self.task_list = QListWidget()
        self.task_list.setObjectName("historyList")
        self.resume_button = QPushButton("继续任务")
        self.review_button = QPushButton("查看图片")
        self.review_button.setProperty("primary", True)
        self.open_button = QPushButton("打开目录")
        self.remove_button = QPushButton("移出历史")
        self.remove_button.setToolTip("仅移出列表，原有任务文件保留")
        self.resume_button.clicked.connect(self.resume_selected)
        self.review_button.clicked.connect(self.review_selected)
        self.open_button.clicked.connect(self.open_selected)
        self.remove_button.clicked.connect(self.remove_selected)
        actions = QHBoxLayout()
        for button in (self.resume_button, self.review_button, self.open_button, self.remove_button):
            actions.addWidget(button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 12, 8, 12)
        layout.setSpacing(14)
        layout.addWidget(page_header("历史任务", "从上次进度继续，或回到配图审核。历史结果与原件始终保留。"))
        layout.addWidget(self.search_edit)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.task_list)
        layout.addLayout(actions)
        self.task_list.currentRowChanged.connect(self._update_actions)
        self.refresh()

    def refresh(self) -> None:
        self.records = list(self.store.list_tasks())
        self._filter()

    def _filter(self, *_args) -> None:
        query = self.search_edit.text().strip().casefold()
        self._visible_records = [record for record in self.records if query in record.issue_id.casefold()]
        self.task_list.clear()
        for record in self._visible_records:
            try:
                created = datetime.fromisoformat(record.created_at.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                created = "创建时间未知"
            label = f"第 {record.issue_id} 期    ·    {created}"
            if record.imported:
                label += "    ·    已导入"
            label += f"\n{record.task_id}"
            item = self.task_list.addItem(label)
            _ = item
            self.task_list.item(self.task_list.count() - 1).setData(256, record.task_id)
        self.empty_label.setVisible(not self._visible_records)
        self.empty_label.setText("没有匹配的期号" if query else "暂无历史任务，可在新建任务页导入已有结果。")
        self._update_actions()

    def _update_actions(self, *_args) -> None:
        selected = self.selected_record()
        for button in (self.review_button, self.open_button, self.remove_button):
            button.setEnabled(selected is not None)
        self.resume_button.setEnabled(selected is not None and not selected.imported)

    def selected_record(self) -> TaskRecord | None:
        row = self.task_list.currentRow()
        return self._visible_records[row] if 0 <= row < len(self._visible_records) else None

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
