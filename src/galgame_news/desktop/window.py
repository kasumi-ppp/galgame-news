"""Main window and five-page navigation."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QVBoxLayout,
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

from .controller import DesktopController
from .ui import icon


class MainWindow(QMainWindow):
    PAGE_NAMES = ("新建任务", "抓取进度", "图片审核", "历史任务", "设置")

    def __init__(self, controller: DesktopController | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("mainWindow")
        self.setWindowTitle("Galgame 新闻工具箱")
        self.resize(1280, 820)
        self.setMinimumSize(1100, 720)
        self.controller = controller or DesktopController(parent=self)
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.nav_list = self.navigation
        self.navigation.setMinimumWidth(155)
        for name, symbol in zip(self.PAGE_NAMES, ("new", "progress", "review", "history", "settings")):
            self.navigation.addItem(QListWidgetItem(icon(symbol), name))
        self.pages = QStackedWidget()
        self.stacked_widget = self.pages
        self.new_task_page = self.controller.new_task_page
        self.progress_page = self.controller.progress_page
        self.review_page = self.controller.review_page
        self.history_page = self.controller.history_page
        self.settings_page = self.controller.settings_page
        self._close_pending = False
        self._close_signals_connected = False
        for page in (self.new_task_page, self.progress_page, self.review_page, self.history_page, self.settings_page):
            self.pages.addWidget(page)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(192)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 26, 16, 18)
        brand = QLabel("Galgame\n新闻工具箱")
        brand.setObjectName("brand")
        side.addWidget(brand)
        tagline = QLabel("采集 · 筛选 · 审阅")
        tagline.setObjectName("muted")
        side.addWidget(tagline)
        side.addSpacing(25)
        side.addWidget(self.navigation, 1)
        footer = QLabel("让每张配图都有出处")
        footer.setObjectName("muted")
        footer.setWordWrap(True)
        side.addWidget(footer)
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(18)
        layout.addWidget(sidebar)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)
        self.controller.task_started.connect(lambda *_: self.navigation.setCurrentRow(1))
        self.controller.review_loaded.connect(lambda *_: self.navigation.setCurrentRow(2))

    def _connect_deferred_close(self) -> None:
        if self._close_signals_connected:
            return
        self._close_signals_connected = True
        for signal in (self.controller.task_finished, self.controller.task_cancelled, self.controller.task_failed, self.controller.history_changed):
            signal.connect(self._schedule_deferred_close)

    def _schedule_deferred_close(self, *_args) -> None:
        if self._close_pending:
            QTimer.singleShot(0, self._finish_deferred_close)

    def _finish_deferred_close(self) -> None:
        if self._close_pending and not self.controller.is_running:
            self._close_pending = False
            self.close()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._close_pending:
            event.ignore()
            return
        if self.controller.is_running:
            self._close_pending = True
            self._connect_deferred_close()
            if self.controller.close():
                self._close_pending = False
                event.accept()
            else:
                event.ignore()
            return
        self.controller.close(timeout_ms=0)
        event.accept()


__all__ = ["MainWindow"]
