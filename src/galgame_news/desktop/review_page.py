"""Review page with deterministic decisions and lazy thumbnail previews."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..review import ReviewDecision, ReviewSession
from .thumbnail_cache import ThumbnailCache


class ReviewPage(QWidget):
    decision_changed = Signal(str, str)
    retry_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("reviewPage")
        self.session: ReviewSession | None = None
        self.cache: ThumbnailCache[QPixmap] = ThumbnailCache(64)
        self._items: list[Any] = []
        self._current_pixmap = QPixmap()
        self.zoom_factor = 1.0
        self.tabs = QTabWidget()
        self.tabs.addTab(QWidget(), "Accepted")
        self.tabs.addTab(QWidget(), "Pending")
        self.tabs.addTab(QWidget(), "Rejected")
        self.tabs.addTab(QWidget(), "Videos")
        self.tabs.setCurrentIndex(1)
        self.tabs.currentChanged.connect(lambda _index: self.refresh())

        self.media_list = QListWidget()
        self.media_list.setObjectName("reviewMediaList")
        self.media_list.currentRowChanged.connect(self._select_row)
        self.preview = QLabel("Select media")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(240, 180)
        self.preview.setScaledContents(False)
        self.metadata = QLabel()
        self.metadata.setWordWrap(True)
        self.source_button = QPushButton("Open source")
        self.play_button = QPushButton("Play video")
        self.retry_button = QPushButton("Retry this news")
        self.retry_url_edit = QLineEdit()
        self.retry_url_edit.setObjectName("retryOfficialUrlEdit")
        self.retry_url_edit.setPlaceholderText("Optional official URL for retry")
        # ``official_url_edit`` is a descriptive alias for integrations that
        # do not know the shorter button-oriented name.
        self.official_url_edit = self.retry_url_edit
        self.accept_button = QPushButton("Accept")
        self.reject_button = QPushButton("Reject")
        self.pending_button = QPushButton("Pending")
        self.source_button.clicked.connect(self.open_source)
        self.play_button.clicked.connect(self.play_video)
        self.retry_button.clicked.connect(self.retry_selected)
        self.accept_button.clicked.connect(self.accept_selected)
        self.reject_button.clicked.connect(self.reject_selected)
        self.pending_button.clicked.connect(self.pending_selected)
        for button in (self.source_button, self.play_button, self.retry_button, self.accept_button, self.reject_button, self.pending_button):
            button.setEnabled(False)

        actions = QHBoxLayout()
        for button in (self.accept_button, self.reject_button, self.pending_button, self.source_button, self.play_button, self.retry_button):
            actions.addWidget(button)
        actions.addWidget(self.retry_url_edit)
        details = QVBoxLayout()
        details.addWidget(self.preview, 2)
        details.addWidget(self.metadata, 1)
        details.addLayout(actions)
        body = QHBoxLayout()
        body.addWidget(self.media_list, 1)
        body.addLayout(details, 2)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addLayout(body)

    def set_session(self, session: ReviewSession | None) -> None:
        self.session = session
        self.cache.clear()
        self.zoom_factor = 1.0
        self.refresh()

    def failed_candidates(self, news_id: str | None = None) -> list[Any]:
        """Return failed pending candidates in ReviewSession's stable order."""

        if self.session is None:
            return []
        return list(self.session.failed_images(news_id))

    def _visible_candidates(self) -> list[Any]:
        if self.session is None:
            return []
        index = self.tabs.currentIndex()
        if index == 0:
            return list(self.session.accepted_images())
        if index == 2:
            return list(self.session.rejected_images())
        if index == 3:
            return [*self.session.accepted_videos(), *self.session.pending_videos()]
        return list(self.session.pending_images())

    def refresh(self) -> None:
        self.media_list.blockSignals(True)
        self.media_list.clear()
        self._items = self._visible_candidates()
        for candidate in self._items:
            title = getattr(candidate, "title", None) or getattr(candidate, "news_id", "media")
            item = QListWidgetItem(f"{title} · {getattr(candidate, 'id', '')}")
            item.setData(Qt.ItemDataRole.UserRole, str(getattr(candidate, "id", "")))
            self.media_list.addItem(item)
        self.media_list.blockSignals(False)
        if self._items:
            self.media_list.setCurrentRow(0)
        else:
            self._clear_details()

    def _selected(self) -> Any | None:
        row = self.media_list.currentRow()
        return self._items[row] if 0 <= row < len(self._items) else None

    def _clear_details(self) -> None:
        self.preview.setText("Select media")
        self.preview.setPixmap(QPixmap())
        self._current_pixmap = QPixmap()
        self.metadata.clear()
        for button in (self.source_button, self.play_button, self.retry_button, self.accept_button, self.reject_button, self.pending_button):
            button.setEnabled(False)

    def _select_row(self, row: int) -> None:
        candidate = self._items[row] if 0 <= row < len(self._items) else None
        if candidate is None:
            self._clear_details()
            return
        media_id = str(getattr(candidate, "id", ""))
        local = getattr(candidate, "local_path", None)
        source_path = Path(local) if local else None
        if source_path is not None and not source_path.is_absolute() and self.session is not None:
            source_path = self.session.output_dir / source_path
        if source_path is not None and source_path.is_file():
            key = str(source_path.resolve())
            pixmap = self.cache.get(key, lambda: QPixmap(str(source_path)))
            if pixmap is not None and not pixmap.isNull():
                self._current_pixmap = pixmap
                self._render_preview()
            else:
                self.preview.setText("Preview unavailable")
        else:
            self.preview.setText("Preview unavailable")
        score = getattr(candidate, "score", None)
        score_value = getattr(score, "total", None) if score is not None else None
        source = str(getattr(candidate, "source_url", "") or getattr(candidate, "image_url", ""))
        self.metadata.setText(
            f"News: {getattr(candidate, 'news_id', '')}\n"
            f"ID: {media_id}\n"
            f"Score: {score_value if score_value is not None else 'n/a'}\n"
            f"Source: {source}"
        )
        self.source_button.setEnabled(bool(source))
        self.play_button.setEnabled(bool(source_path and source_path.is_file() and getattr(candidate, "video_url", None)))
        self.retry_button.setEnabled(bool(getattr(candidate, "news_id", None)))
        self.accept_button.setEnabled(True)
        self.reject_button.setEnabled(True)
        self.pending_button.setEnabled(True)

    def _render_preview(self) -> None:
        if self._current_pixmap.isNull():
            return
        target = self.preview.size() * self.zoom_factor
        self.preview.setPixmap(
            self._current_pixmap.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def zoom_in(self) -> float:
        self.zoom_factor = min(4.0, round(self.zoom_factor * 1.25, 3))
        self._render_preview()
        return self.zoom_factor

    def zoom_out(self) -> float:
        self.zoom_factor = max(0.25, round(self.zoom_factor / 1.25, 3))
        self._render_preview()
        return self.zoom_factor

    def reset_zoom(self) -> float:
        self.zoom_factor = 1.0
        self._render_preview()
        return self.zoom_factor

    def _set_selected_decision(self, decision: ReviewDecision) -> None:
        if self.session is None:
            return
        candidates = self._selected_items()
        for candidate in candidates:
            self.session.set_decision(str(candidate.id), decision)
            self.decision_changed.emit(str(candidate.id), decision.value)
        self.refresh()

    def _selected_items(self) -> list[Any]:
        rows = sorted({index.row() for index in self.media_list.selectedIndexes()})
        if not rows and self._selected() is not None:
            return [self._selected()]
        return [self._items[row] for row in rows if 0 <= row < len(self._items)]

    def accept_selected(self) -> None:
        self._set_selected_decision(ReviewDecision.ACCEPTED)

    def reject_selected(self) -> None:
        self._set_selected_decision(ReviewDecision.REJECTED)

    def pending_selected(self) -> None:
        self._set_selected_decision(ReviewDecision.PENDING)

    def open_source(self) -> None:
        candidate = self._selected()
        source = getattr(candidate, "source_url", None) or getattr(candidate, "image_url", None) if candidate else None
        if source:
            QDesktopServices.openUrl(QUrl(str(source)))

    def play_video(self) -> None:
        candidate = self._selected()
        local = getattr(candidate, "local_path", None) if candidate else None
        if not local:
            return
        path = Path(local)
        if not path.is_absolute() and self.session is not None:
            path = self.session.output_dir / path
        if path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def retry_selected(self) -> None:
        candidate = self._selected()
        if candidate is not None and getattr(candidate, "news_id", None):
            self.retry_requested.emit(str(candidate.news_id))

    def retry_url(self) -> str:
        """Return the optional manually supplied official page URL."""

        return self.retry_url_edit.text().strip()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        key = event.key()
        if key == Qt.Key.Key_A:
            self.accept_selected()
            event.accept()
            return
        if key == Qt.Key.Key_R:
            self.reject_selected()
            event.accept()
            return
        if key == Qt.Key.Key_P:
            self.pending_selected()
            event.accept()
            return
        super().keyPressEvent(event)


__all__ = ["ReviewPage"]
