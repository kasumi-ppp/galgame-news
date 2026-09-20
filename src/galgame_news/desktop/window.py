"""Main window and five-page navigation."""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

from .controller import DesktopController


class MainWindow(QMainWindow):
    PAGE_NAMES = ("New Task", "Progress", "Review", "History", "Settings")

    def __init__(self, controller: DesktopController | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("mainWindow")
        self.setWindowTitle("Galgame News Toolbox")
        self.resize(1100, 720)
        self.controller = controller or DesktopController(parent=self)
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.nav_list = self.navigation
        self.navigation.setFixedWidth(170)
        for name in self.PAGE_NAMES:
            self.navigation.addItem(QListWidgetItem(name))
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
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.addWidget(self.navigation)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)

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
