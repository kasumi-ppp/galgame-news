"""Three-column media review with bounded asynchronous image previews."""
from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QEvent, QPoint, QSize, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QImage, QKeyEvent, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QTreeWidget, QTreeWidgetItem,
    QPushButton, QScrollArea, QSplitter, QTabWidget, QTextEdit, QToolButton,
    QVBoxLayout, QWidget,
)

from ..review import ReviewDecision, ReviewSession
from .async_export import AsyncExport
from .async_images import AsyncImages, ImageRequest
from .i18n import code, image_type_label, reason_label, status_label
from .thumbnail_cache import ThumbnailCache
from .ui import page_header
from ..delivery.helpers import section_prefix


class ReviewPage(QWidget):
    decision_changed = Signal(str, str)
    retry_requested = Signal(str)
    supplement_requested = Signal(str, str)
    supplement_cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("reviewPage")
        self.session: ReviewSession | None = None
        self.cache: ThumbnailCache[QPixmap] = ThumbnailCache(128)
        self._items: list[Any] = []
        self._news: dict[str, dict[str, Any]] = {}
        self._row_paths = {}
        self._icon_rows: set[int] = set()
        self._refresh_generation = 0
        self._current_pixmap = QPixmap()
        self._preview_key = ""
        self._retry_context: tuple[int, str] | None = None
        self._supplement_busy = False
        self._supplement_context: tuple[int, str] | None = None
        self.zoom_factor = 1.0
        self._export_requested_count = 0
        self.images = AsyncImages(self)
        self.images.ready.connect(self._image_ready)
        self.exporter = AsyncExport(self)
        self.exporter.finished.connect(self._export_done)
        self._thumbnail_timer = QTimer(self)
        self._thumbnail_timer.setSingleShot(True)
        self._thumbnail_timer.setInterval(35)
        self._thumbnail_timer.timeout.connect(self._load_visible)
        self.news_list = QTreeWidget()
        self.news_list.setHeaderHidden(True)
        self.news_list.setObjectName("reviewNewsList")
        self.news_list.setMinimumWidth(170)
        self.news_list.setWordWrap(True)
        self.news_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.news_list.currentItemChanged.connect(self._news_item_changed)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("reviewTabs")
        for label in ("已选", "未候选／待复核", "已排除", "视频"):
            self.tabs.addTab(QWidget(), label)
        self.tabs.setCurrentIndex(1)
        self.tabs.setMaximumHeight(48)
        self.tabs.currentChanged.connect(lambda _: self.refresh())
        self.media_list = QListWidget()
        self.media_list.setObjectName("reviewMediaList")
        self.media_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.media_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.media_list.setMovement(QListWidget.Movement.Static)
        self.media_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.media_list.setIconSize(QSize(148, 100))
        self.media_list.setGridSize(QSize(174, 166))
        self.media_list.setSpacing(6)
        self.media_list.setWordWrap(True)
        self.media_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.media_list.setUniformItemSizes(True)
        self.media_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.media_list.currentRowChanged.connect(self._select_row)
        self.media_list.itemSelectionChanged.connect(self._selection_changed)
        self.media_list.verticalScrollBar().valueChanged.connect(self._schedule_thumbnails)
        self.media_list.viewport().installEventFilter(self)
        self.media_list.installEventFilter(self)
        self.grid_summary = QLabel("尚未加载媒体")
        self.grid_summary.setObjectName("muted")
        self.preview = QLabel("载入抓取结果后，在中间选择媒体")
        self.preview.setObjectName("reviewPreview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(240, 200)
        self.preview.setWordWrap(True)
        self.preview.setScaledContents(False)
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setObjectName("reviewPreviewScroll")
        self.preview_scroll.setWidget(self.preview)
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setMinimumHeight(225)
        self.preview_scroll.viewport().installEventFilter(self)
        self.preview_scroll.setStyleSheet("QScrollArea#reviewPreviewScroll, QLabel#reviewPreview { background: #272a32; color: #e5e7eb; border-radius: 8px; }")
        self.metadata = QLabel()
        self.metadata.setWordWrap(True)
        self.metadata.setTextFormat(Qt.TextFormat.PlainText)
        self.metadata.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.selection_label = QLabel("可按 Ctrl / Shift 多选；A 已选 · R 排除 · P 待复核")
        self.selection_label.setObjectName("muted")
        self.selection_label.setWordWrap(True)
        self.source_button = QPushButton("打开来源")
        self.official_button = QPushButton("打开官网")
        self.original_button = QPushButton("打开原图")
        self.play_button = QPushButton("播放视频")
        self.retry_button = QPushButton("重试本条新闻")
        self.retry_url_edit = QLineEdit()
        self.retry_url_edit.setObjectName("retryOfficialUrlEdit")
        self.retry_url_edit.setPlaceholderText("重试官网地址（选填）")
        self.official_url_edit = self.retry_url_edit
        self.supplement_url_edit = QLineEdit()
        self.supplement_url_edit.setObjectName("supplementUrlEdit")
        self.supplement_url_edit.setPlaceholderText("补充图片网页地址（选填）")
        self.supplement_button = QPushButton("抓取补充图片")
        self.supplement_cancel_button = QPushButton("取消补充")
        self.supplement_cancel_button.setEnabled(False)
        self.supplement_button.clicked.connect(self.request_supplement)
        self.supplement_cancel_button.clicked.connect(self.supplement_cancel_requested.emit)
        self.accept_button = QPushButton("已选  A")
        self.accept_button.setObjectName("primaryButton")
        self.accept_button.setProperty("primary", True)
        self.reject_button = QPushButton("排除  R")
        self.pending_button = QPushButton("待复核  P")
        self.export_button = QPushButton("导出图片（含未候选）")
        self.export_button.setObjectName("primaryButton")
        self.export_button.setProperty("primary", True)
        self.export_button.setEnabled(False)
        self.open_images_button = QPushButton("打开图片目录")
        self.open_images_button.setEnabled(False)
        self.open_images_button.clicked.connect(self.open_images_directory)
        self.export_status = QLabel()
        self.export_status.setObjectName("muted")
        self.export_status.setWordWrap(True)
        self.operation_status = QLabel()
        self.operation_status.setObjectName("muted")
        self.operation_status.setWordWrap(True)
        self.raw_toggle = QToolButton()
        self.raw_toggle.setText("技术详情")
        self.raw_toggle.setCheckable(True)
        self.raw_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.raw_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.raw_details = QTextEdit()
        self.raw_details.setReadOnly(True)
        self.raw_details.setVisible(False)
        self.raw_details.setMaximumHeight(155)
        self.raw_toggle.toggled.connect(self._toggle_raw)
        for button, callback in ((self.source_button, self.open_source), (self.official_button, self.open_official),
                                 (self.original_button, self.open_original), (self.play_button, self.play_video),
                                 (self.retry_button, self.retry_selected), (self.accept_button, self.accept_selected),
                                 (self.reject_button, self.reject_selected), (self.pending_button, self.pending_selected),
                                 (self.export_button, self.export_final)):
            button.clicked.connect(callback)
        self.zoom_in_button = QPushButton("放大 +")
        self.zoom_out_button = QPushButton("缩小 −")
        self.zoom_reset_button = QPushButton("适应窗口")
        self.zoom_in_button.clicked.connect(self.zoom_in)
        self.zoom_out_button.clicked.connect(self.zoom_out)
        self.zoom_reset_button.clicked.connect(self.reset_zoom)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("本期新闻"))
        left_layout.addWidget(self.news_list)
        grid = QWidget()
        grid_layout = QVBoxLayout(grid)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.addWidget(self.tabs)
        grid_layout.addWidget(self.grid_summary)
        grid_layout.addWidget(self.media_list, 1)
        grid_layout.addWidget(self.selection_label)
        # Keep long evidence and expanded raw diagnostics inside the pane's
        # viewport; the page-level export footer stays accessible at 720px.
        right = QScrollArea()
        self.details_scroll = right
        right.setObjectName("reviewDetailsScroll")
        right.setWidgetResizable(True)
        right.setFrameShape(QScrollArea.Shape.NoFrame)
        right.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right.setMinimumWidth(340)
        detail_content = QWidget()
        right.setWidget(detail_content)
        details = QVBoxLayout(detail_content)
        details.setContentsMargins(0, 0, 0, 0)
        details.addWidget(QLabel("媒体预览与复核依据"))
        details.addWidget(self.preview_scroll, 1)
        for buttons in ((self.zoom_out_button, self.zoom_in_button, self.zoom_reset_button),
                        (self.accept_button, self.reject_button, self.pending_button),
                        (self.source_button, self.official_button),
                        (self.original_button, self.play_button)):
            actions = QHBoxLayout()
            for button in buttons:
                actions.addWidget(button)
            details.addLayout(actions)
        details.addWidget(self.metadata)
        details.addWidget(self.raw_toggle)
        details.addWidget(self.raw_details)
        retry_row = QHBoxLayout()
        retry_row.addWidget(self.retry_url_edit, 1)
        retry_row.addWidget(self.retry_button)
        details.addLayout(retry_row)
        supplement_row = QHBoxLayout()
        supplement_row.addWidget(self.supplement_url_edit, 1)
        supplement_row.addWidget(self.supplement_button)
        details.addLayout(supplement_row)
        details.addWidget(self.supplement_cancel_button)
        details.addWidget(self.operation_status)
        for widget in (left, grid, right):
            self.splitter.addWidget(widget)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 1)
        self.splitter.setSizes([195, 430, 460])
        layout = QVBoxLayout(self)
        layout.addWidget(page_header("图片审核", "按新闻复核候选图片与视频，保留适合本期内容的媒体。"))
        layout.addWidget(self.splitter, 1)
        export_row = QHBoxLayout()
        export_row.addWidget(self.export_status, 1)
        export_row.addWidget(self.open_images_button)
        export_row.addWidget(self.export_button)
        layout.addLayout(export_row)
        self._shortcuts = []
        for key, callback in (("A", self.accept_selected), ("R", self.reject_selected), ("P", self.pending_selected)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)
        self._clear_details()

    def set_session(self, session: ReviewSession | None) -> None:
        self.session = session
        self._supplement_busy = False
        self._supplement_context = None
        self.supplement_url_edit.clear()
        self.supplement_url_edit.setEnabled(True)
        self.supplement_cancel_button.setEnabled(False)
        self._retry_context = None
        self.retry_url_edit.clear()
        self.images.invalidate()
        self.cache.clear()
        self.zoom_factor = 1.0
        self.export_status.clear()
        if session is not None:
            self.export_status.setText(
                f"来源目录：{session.output_dir}\n导出目录：{session.task_root / 'final' / 'images'}"
            )
        self.operation_status.clear()
        self.export_button.setEnabled(session is not None and self.exporter.session is None)
        self.open_images_button.setEnabled(session is not None)
        self.news_list.blockSignals(True)
        self.news_list.clear()
        self._news = {str(item["news_id"]): item for item in session.news_items} if session else {}
        all_news = QTreeWidgetItem([f"全部新闻（{len(self._news)}）"])
        all_news.setData(0, Qt.ItemDataRole.UserRole, None)
        self.news_list.addTopLevelItem(all_news)
        categories = {prefix: QTreeWidgetItem([label]) for prefix, label in (("x", "新作"), ("h", "汉化"), ("z", "周边"))}
        for prefix, node in categories.items():
            node.setData(0, Qt.ItemDataRole.UserRole, ("section", prefix))
            all_news.addChild(node)
        for news_id, news in self._news.items():
            title = str(news.get("title") or news_id)
            item = QTreeWidgetItem([f"{news.get('sequence', '')}  {title[:90]}" + ("…" if len(title) > 90 else "")])
            item.setToolTip(0, title)
            item.setData(0, Qt.ItemDataRole.UserRole, ("news", news_id))
            categories[section_prefix(str(news.get("section", "")))].addChild(item)
        self.news_list.expandAll()
        self.news_list.setCurrentItem(all_news)
        self.news_list.blockSignals(False)
        self.refresh()

    def _news_filter(self) -> str | None:
        item = self.news_list.currentItem()
        value = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        return value[1] if isinstance(value, tuple) and value[0] == "news" else None

    def _selected_section(self) -> str | None:
        item = self.news_list.currentItem()
        value = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        return value[1] if isinstance(value, tuple) and value[0] == "section" else None

    def _news_item_changed(self, *_args: Any) -> None:
        context = (id(self.session), str(self._news_filter() or ""))
        if context != self._supplement_context:
            self.supplement_url_edit.clear()
            self._supplement_context = context
        self.refresh()

    def failed_candidates(self, news_id: str | None = None) -> list[Any]:
        return list(self.session.failed_images(news_id)) if self.session else []

    def _visible_candidates(self) -> list[Any]:
        if self.session is None:
            return []
        index, news_id = self.tabs.currentIndex(), self._news_filter()
        section = self._selected_section()
        if section:
            allowed = {key for key, news in self._news.items() if section_prefix(str(news.get("section", ""))) == section}
            if index == 0:
                candidates = self.session.accepted_images()
                return [c for c in candidates if str(c.news_id) in allowed]
            if index == 2:
                candidates = self.session.rejected_images()
                return [c for c in candidates if str(c.news_id) in allowed]
            if index == 3:
                candidates = [*self.session.accepted_videos(), *self.session.pending_videos()]
                return [c for c in candidates if str(c.news_id) in allowed]
            candidates = self.session.pending_images()
            return [c for c in candidates if str(c.news_id) in allowed]
        if index == 0:
            return list(self.session.accepted_images(news_id))
        if index == 2:
            return list(self.session.rejected_images(news_id))
        if index == 3:
            return [*self.session.accepted_videos(news_id), *self.session.pending_videos(news_id)]
        return list(self.session.pending_images(news_id))

    def _title(self, candidate: Any) -> str:
        return str(getattr(candidate, "title", None) or self._news.get(str(candidate.news_id), {}).get("title") or candidate.news_id)

    def _badges(self, candidate: Any) -> list[str]:
        signals = getattr(candidate, "signals", {})
        reasons = [code(value) for value in getattr(candidate, "review_reasons", [])]
        badges = []
        if code(getattr(candidate, "image_type", "")) == "game_cg":
            badges.append("CG")
        if code(getattr(candidate, "source_type", "")) == "official_x" or "x_source" in reasons or "x.com/" in candidate.source_url or "twitter.com/" in candidate.source_url:
            badges.append("X")
        if any("duplicate" in value for value in reasons) or signals.get("historical_duplicate") or signals.get("duplicate_of") or getattr(getattr(candidate, "score", None), "duplicate_penalty", 0):
            badges.append("重复")
        if code(getattr(candidate, "curation_status", "")) == "invalid" or signals.get("invalid_reason"):
            badges.append("无效")
        if str(candidate.id) in (self.session._failed_ids if self.session else set()) or getattr(candidate, "failure_reason", None):
            badges.append("失败")
        return badges

    @staticmethod
    def _localization_summary(candidate: Any) -> str | None:
        provenance = getattr(candidate, "localization_provenance", None)
        signals = getattr(candidate, "signals", {}) or {}

        def field(name: str) -> str:
            value = getattr(provenance, name, None) if provenance is not None else None
            if value in (None, ""):
                value = signals.get(f"localization_{name}", "")
            return str(value or "")

        source = field("source").casefold()
        work_id = field("work_id")
        if not source and not work_id:
            return None
        source_label = {"steam": "Steam", "vndb": "VNDB"}.get(source, source or "未知来源")
        binding_label = {"confirmed": "当前作品", "reference": "关联参考"}.get(field("binding").casefold(), "待确认")
        resolution_label = {"full": "原图", "thumbnail": "缩略图"}.get(field("resolution").casefold(), "尺寸待确认")
        parts = [f"汉化来源：{source_label}"]
        if work_id:
            parts.append(f"作品 ID {work_id}")
        parts.extend((binding_label, resolution_label))
        return " · ".join(parts)

    def refresh(self, preserve_scroll: bool = False, preserve_position: bool = False) -> None:
        preserve_scroll = preserve_scroll or preserve_position
        self._refresh_generation += 1
        refresh_generation = self._refresh_generation
        session = self.session
        view = (id(session), self._news_filter(), self.tabs.currentIndex())
        scroll_value = self.media_list.verticalScrollBar().value() if preserve_scroll else 0
        previous = str(self._selected().id) if self._selected() else ""
        previous_row = self.media_list.currentRow()
        self.images.invalidate()
        self._preview_key = ""
        self.media_list.blockSignals(True)
        self.media_list.clear()
        self._items = self._visible_candidates()
        self._row_paths = {}
        self._icon_rows.clear()
        current = min(max(previous_row, 0), max(0, len(self._items) - 1)) if preserve_scroll else 0
        for row, candidate in enumerate(self._items):
            title = self._title(candidate)
            kind = "视频" if getattr(candidate, "video_url", None) else image_type_label(getattr(candidate, "image_type", None))
            badges = " · ".join(self._badges(candidate))
            displayed_kind = "游戏画面" if "CG" in self._badges(candidate) else kind
            item = QListWidgetItem(f"{title[:32]}{'…' if len(title) > 32 else ''}\n{displayed_kind}" + (f" · {badges}" if badges else ""))
            item.setData(Qt.ItemDataRole.UserRole, str(candidate.id))
            item.setToolTip(title + "\n" + "、".join(reason_label(value) for value in getattr(candidate, "review_reasons", [])))
            item.setSizeHint(QSize(174, 166))
            self.media_list.addItem(item)
            if self.session and not getattr(candidate, "video_url", None):
                path = self.session._source_for(candidate)
                if path:
                    self._row_paths[row] = path
            if str(candidate.id) == previous:
                current = row
        self.media_list.blockSignals(False)
        self.grid_summary.setText(f"{self.tabs.tabText(self.tabs.currentIndex())} · {len(self._items)} 项" if self._items else "本分类暂无媒体，切换新闻或分类查看")
        if self._items:
            self.media_list.setCurrentRow(current)
        else:
            self._clear_details()
        self._schedule_thumbnails()
        if preserve_scroll:
            def restore_scroll() -> None:
                if (refresh_generation != self._refresh_generation or self.session is not session
                        or view != (id(self.session), self._news_filter(), self.tabs.currentIndex())):
                    return
                bar = self.media_list.verticalScrollBar()
                bar.setValue(min(scroll_value, bar.maximum()))

            QTimer.singleShot(0, restore_scroll)

    def _selected(self) -> Any | None:
        row = self.media_list.currentRow()
        return self._items[row] if 0 <= row < len(self._items) else None

    def _clear_details(self) -> None:
        self._bind_retry_context(self._news_filter())
        self._preview_key = ""
        self._current_pixmap = QPixmap()
        self.preview.setPixmap(QPixmap())
        self.preview.setText("本分类暂无媒体" if self.session else "载入抓取结果后，在中间选择媒体")
        self.preview.setMinimumSize(240, 200)
        self.metadata.clear()
        self.raw_details.clear()
        for button in (self.source_button, self.official_button, self.original_button, self.play_button, self.retry_button, self.accept_button, self.reject_button, self.pending_button, self.zoom_in_button, self.zoom_out_button, self.zoom_reset_button):
            button.setEnabled(False)
        self.supplement_button.setEnabled(bool(self.session and self._news_filter()) and not self._supplement_busy and self.exporter.session is not self.session)
        self.retry_button.setEnabled(bool(self.session and self._news_filter()) and not self._supplement_busy and self.exporter.session is not self.session)

    def _select_row(self, row: int) -> None:
        candidate = self._items[row] if 0 <= row < len(self._items) else None
        if candidate is None:
            self._clear_details()
            return
        self._bind_retry_context(str(candidate.news_id))
        self._current_pixmap = QPixmap()
        self._preview_key = ""
        self.zoom_factor = 1.0
        self.preview.setMinimumSize(240, 200)
        self.preview.setPixmap(QPixmap())
        path = self.session._source_for(candidate) if self.session else None
        is_video = bool(getattr(candidate, "video_url", None))
        if path and not is_video:
            self.preview.setText("正在加载预览…")
            self._preview_key = f"preview:{candidate.id}:{path}"
            self.images.request_preview(ImageRequest(self._preview_key, path, QSize(2048, 2048), True))
        else:
            self.preview.setText("视频文件 · 点击播放视频" if is_video and path else "预览不可用 · 可打开来源查看")
        score = getattr(getattr(candidate, "score", None), "total", None)
        reasons = [*getattr(candidate, "review_reasons", []), *getattr(candidate, "selection_reasons", [])]
        signals = getattr(candidate, "signals", {})
        if signals.get("invalid_reason"):
            reasons.append("invalid:" + str(signals["invalid_reason"]))
        reason_text = "；".join(dict.fromkeys(reason_label(value) for value in reasons)) or "暂无额外复核原因"
        kind = "视频" if is_video else image_type_label(getattr(candidate, "image_type", None))
        decision = status_label(self.session.decision(str(candidate.id))) if self.session else ""
        localization_summary = self._localization_summary(candidate)
        self.metadata.setText(f"{self._title(candidate)[:180]}\n{kind} · {decision} · {getattr(candidate, 'width', None) or '—'} × {getattr(candidate, 'height', None) or '—'}"
                              + (f" · 评分 {score:.1f}" if score is not None else "")
                              + (f"\n{' · '.join(self._badges(candidate))}" if self._badges(candidate) else "")
                              + (f"\n{localization_summary}" if localization_summary else "")
                              + f"\n复核依据：{reason_text}")
        payload = candidate.model_dump(mode="json") if hasattr(candidate, "model_dump") else {"id": str(candidate.id)}
        payload["resolved_local_path"] = str(path) if path else None
        self.raw_details.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        source = self._source_url(candidate)
        host = QUrl(source).host().casefold()
        self.source_button.setText("打开原帖" if host in {"x.com", "twitter.com"} or host.endswith((".x.com", ".twitter.com")) else "打开来源")
        self.source_button.setEnabled(bool(source))
        self.official_button.setEnabled(bool(self._official_url(candidate)))
        self.original_button.setEnabled(bool(getattr(candidate, "image_url", None)))
        self.play_button.setEnabled(bool(path and is_video))
        self.retry_button.setEnabled(bool(candidate.news_id) and self.exporter.session is not self.session)
        self.supplement_button.setEnabled(bool(self._news_filter()) and not self._supplement_busy and self.exporter.session is not self.session)
        self.retry_button.setEnabled(bool(candidate.news_id) and not self._supplement_busy and self.exporter.session is not self.session)
        for button in (self.accept_button, self.reject_button, self.pending_button):
            button.setEnabled(self.exporter.session is not self.session)
        for button in (self.zoom_in_button, self.zoom_out_button, self.zoom_reset_button):
            button.setEnabled(bool(path and not is_video))

    def _selection_changed(self) -> None:
        count = len(self.media_list.selectedIndexes())
        self.selection_label.setText(f"已选择 {count} 项 · Ctrl / Shift 多选；A 已选 · R 排除 · P 待复核")

    def _schedule_thumbnails(self, *_args: Any) -> None:
        self._thumbnail_timer.start()

    def _visible_rows(self) -> list[int]:
        viewport = self.media_list.viewport().rect()
        region = viewport.adjusted(0, -172, 0, 172)
        # Sample the viewport at half-cell intervals, then inspect only nearby
        # model rows. This avoids an O(total imports) scan on every decode.
        sampled = []
        for y in range(0, max(1, viewport.height()), 80):
            for x in range(0, max(1, viewport.width()), 80):
                index = self.media_list.indexAt(QPoint(x, y))
                if index.isValid():
                    sampled.append(index.row())
        columns = max(1, viewport.width() // 174)
        start = max(0, min(sampled) - 2 * columns) if sampled else 0
        stop = min(self.media_list.count(), max(sampled) + 2 * columns + 1) if sampled else min(self.media_list.count(), 48)
        rows = []
        for row in range(start, stop):
            if self.media_list.visualItemRect(self.media_list.item(row)).intersects(region):
                rows.append(row)
                if len(rows) >= self.images.max_pending:
                    break
        return rows

    def _load_visible(self) -> None:
        requests = []
        rows = set(self._visible_rows())
        for row in self._icon_rows - rows:
            item = self.media_list.item(row)
            if item:
                item.setIcon(QIcon())
        self._icon_rows.intersection_update(rows)
        for row in sorted(rows):
            path = self._row_paths.get(row)
            if path is None:
                continue
            key = f"thumbnail:{path}"
            cached = self.cache.get(key)
            if cached is not None:
                self.media_list.item(row).setIcon(QIcon(cached))
                self._icon_rows.add(row)
            else:
                requests.append(ImageRequest(key, path, QSize(328, 224)))
        self.images.request_visible(requests)

    def _image_ready(self, _generation: int, request: ImageRequest, image: QImage, error: str) -> None:
        if request.preview:
            if request.key != self._preview_key:
                return
            if image.isNull():
                self.preview.setText("预览不可用 · 文件无法解码")
                self.raw_details.append(f"\n预览解码错误：{error}")
            else:
                self._current_pixmap = QPixmap.fromImage(image)
                self._render_preview()
            return
        pixmap = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
        self.cache.put(request.key, pixmap)
        for row in self._visible_rows():
            if f"thumbnail:{self._row_paths.get(row)}" == request.key:
                self.media_list.item(row).setIcon(QIcon(pixmap))
                self._icon_rows.add(row)

    def _render_preview(self) -> None:
        if self._current_pixmap.isNull():
            return
        available = self.preview_scroll.viewport().size() - QSize(16, 16)
        target = QSize(max(1, available.width()), max(1, available.height())) * self.zoom_factor
        pixmap = self._current_pixmap.scaled(target, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation)
        self.preview.setMinimumSize(max(240, pixmap.width()), max(200, pixmap.height()))
        self.preview.setPixmap(pixmap)

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
        if self.exporter.session is self.session:
            self.operation_status.setText("正在导出，请等待完成后修改审核决定")
            return
        for candidate in self._selected_items():
            self.session.set_decision(str(candidate.id), decision)
            self.decision_changed.emit(str(candidate.id), decision.value)
        self.refresh(preserve_scroll=True)

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

    def _source_url(self, candidate: Any) -> str:
        return str(getattr(candidate, "news_source_url", None) or getattr(candidate, "parent_source_url", None)
                   or getattr(candidate, "source_url", None) or getattr(candidate, "image_url", None) or "")

    def _official_url(self, candidate: Any) -> str:
        news = self._news.get(str(candidate.news_id), {})
        official = news.get("official_url") or news.get("official_site_url")
        if official:
            return str(official)
        if code(getattr(candidate, "source_type", "")) in {"official_site", "steam"}:
            return str(getattr(candidate, "source_url", ""))
        return ""

    def open_source(self) -> None:
        candidate = self._selected()
        if candidate and self._source_url(candidate):
            QDesktopServices.openUrl(QUrl(self._source_url(candidate)))

    def open_official(self) -> None:
        candidate = self._selected()
        if candidate and self._official_url(candidate):
            QDesktopServices.openUrl(QUrl(self._official_url(candidate)))

    def open_original(self) -> None:
        candidate = self._selected()
        if candidate and getattr(candidate, "image_url", None):
            QDesktopServices.openUrl(QUrl(str(candidate.image_url)))

    def play_video(self) -> None:
        candidate = self._selected()
        path = self.session._source_for(candidate) if self.session and candidate else None
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def retry_selected(self) -> None:
        if self._supplement_busy:
            return
        if self.exporter.session is self.session and self.session is not None:
            self.operation_status.setText("正在导出，请等待完成后重试")
            return
        candidate = self._selected()
        news_id = candidate.news_id if candidate else self._news_filter()
        if not news_id:
            return
        self._bind_retry_context(str(news_id))
        if not self.retry_url() and candidate:
            self.retry_url_edit.setText(self._official_url(candidate))
        elif not self.retry_url():
            news = self._news.get(str(news_id), {})
            self.retry_url_edit.setText(str(news.get("official_url") or news.get("official_site_url") or ""))
        url = QUrl(self.retry_url())
        if url.scheme().casefold() not in {"http", "https"} or not url.host():
            self.operation_status.setText("请输入可访问的官网地址（http 或 https），再重试本条新闻")
            self.retry_url_edit.setFocus()
            return
        self.operation_status.setText("正在重试本条新闻…")
        self.retry_requested.emit(str(news_id))

    def _bind_retry_context(self, news_id: str | None) -> None:
        context = (id(self.session), str(news_id or ""))
        if context != self._retry_context:
            self.retry_url_edit.clear()
            self.operation_status.clear()
            self._retry_context = context

    def show_retry_result(self, success: bool) -> None:
        self.operation_status.setText("重试完成，候选已更新" if success else "重试未完成，请检查官网地址与设置后重试")

    def show_retry_failure(self, *args: Any) -> None:
        self.operation_status.setText("重试失败，请展开技术详情查看原因")
        self.raw_details.setPlainText("\n".join(str(value) for value in args))
        self.raw_toggle.setChecked(True)

    def retry_url(self) -> str:
        return self.retry_url_edit.text().strip()

    def request_supplement(self) -> None:
        if self._supplement_busy or not self.session:
            return
        news_id = self._news_filter()
        if news_id is None:
            return
        url = QUrl(self.supplement_url_edit.text().strip())
        if url.scheme().casefold() not in {"http", "https"} or not url.host():
            self.operation_status.setText("请输入有效的 HTTP 或 HTTPS 网页地址，再抓取补充图片")
            self.supplement_url_edit.setFocus()
            return
        self.operation_status.setText("正在抓取补充图片…")
        self.set_supplement_busy(True)
        self.supplement_requested.emit(str(news_id), url.toString())

    def set_supplement_busy(self, busy: bool) -> None:
        self._supplement_busy = bool(busy)
        self.supplement_url_edit.setEnabled(not self._supplement_busy)
        candidate = self._selected()
        can_act = bool(self.session and self._news_filter() and not self._supplement_busy and self.exporter.session is not self.session)
        self.supplement_button.setEnabled(can_act)
        self.retry_button.setEnabled(bool(self.session and (candidate or self._news_filter())) and not self._supplement_busy and self.exporter.session is not self.session)
        self.export_button.setEnabled(bool(self.session and not self._supplement_busy and self.exporter.session is None))
        self.supplement_cancel_button.setEnabled(self._supplement_busy)

    def show_supplement_result(self, result: Any) -> None:
        self.set_supplement_busy(False)
        if isinstance(result, dict):
            added = int(result.get("added_count", 0) or 0)
            success = int(result.get("success_count", 0) or 0)
            failures = result.get("failures") or []
            failed = max(int(result.get("failed_count", 0) or 0), len(failures))
            status = {"completed": "已完成", "partial": "部分完成", "cancelled": "已取消", "failed": "失败"}.get(
                str(result.get("status") or "").casefold(), str(result.get("status") or "")
            )
            self.operation_status.setText(f"补充图片{status or '完成'}：新增 {added} 项，成功 {success} 项，失败 {failed} 项")
            self.refresh(preserve_position=True)
            failures = result.get("failures") or []
            if failures:
                self.raw_details.setPlainText(json.dumps(failures, ensure_ascii=False, indent=2, default=str))
                self.raw_toggle.setChecked(True)
        else:
            self.operation_status.setText(f"补充图片抓取完成：{result}")
            self.refresh(preserve_position=True)

    def show_supplement_failure(self, error: Any) -> None:
        self.set_supplement_busy(False)
        self.operation_status.setText("补充图片抓取失败，请展开技术详情查看原因")
        self.raw_details.setPlainText(str(error))
        self.raw_toggle.setChecked(True)

    def export_final(self) -> None:
        if self._supplement_busy or self.session is None or not self.exporter.start(self.session):
            return
        self._export_requested_count = len(self.session.accepted_images()) + len(self.session.accepted_videos())
        self.export_button.setEnabled(False)
        self.export_status.setText("正在导出已选媒体；未候选项目将保留为备选…")
        for button in (self.accept_button, self.reject_button, self.pending_button, self.retry_button):
            button.setEnabled(False)
        self.supplement_button.setEnabled(False)

    def _export_done(self, session: ReviewSession, manifest: dict | None, error: str) -> None:
        self.export_button.setEnabled(self.session is not None and not self._supplement_busy)
        if session is not self.session:
            return
        if manifest is not None:
            exported = int(manifest.get("image_count", 0)) + int(manifest.get("video_count", 0))
            skipped = max(0, self._export_requested_count - exported)
            pending = int(manifest.get("pending_image_count", 0))
            detail = f"；另有 {skipped} 项已选媒体未导出，请检查本地文件" if skipped else ""
            self.export_status.setText(
                f"已导出 {manifest.get('image_count', 0)} 张图片、{manifest.get('video_count', 0)} 个视频；"
                f"未候选备选 {pending} 张{detail}\n来源目录：{session.output_dir}"
                f"\n导出目录：{session.task_root / 'final' / 'images'}"
            )
            self.raw_details.setPlainText(json.dumps(manifest, ensure_ascii=False, indent=2))
        else:
            self.export_status.setText("导出失败，请展开技术详情查看原因")
            self.raw_details.setPlainText(error)
            self.raw_toggle.setChecked(True)
        if self._selected():
            for button in (self.accept_button, self.reject_button, self.pending_button, self.retry_button):
                button.setEnabled(True)
        self.set_supplement_busy(self._supplement_busy)

    def _toggle_raw(self, checked: bool) -> None:
        self.raw_details.setVisible(checked)
        self.raw_toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)

    def open_images_directory(self) -> None:
        if self.session is None:
            return
        root = self.session.task_root
        source = self.session.output_dir
        candidates = []
        if source.name.casefold() == "raw":
            candidates.append(source.parent / "final" / "images")
        candidates.extend((source / "images", root / "final" / "images", root / "raw" / "images", source))
        target = next((path for path in candidates if path.is_dir()), root)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if watched is self.media_list and event.type() in {QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress}:
            callback = {Qt.Key.Key_A: self.accept_selected, Qt.Key.Key_R: self.reject_selected,
                        Qt.Key.Key_P: self.pending_selected}.get(event.key())
            if callback and not event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier):
                if event.type() == QEvent.Type.KeyPress:
                    callback()
                event.accept()
                return True
        if watched is self.media_list.viewport() and event.type() in {QEvent.Type.Resize, QEvent.Type.Show}:
            self._schedule_thumbnails()
        if watched is self.preview_scroll.viewport() and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._render_preview)
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        callback = {Qt.Key.Key_A: self.accept_selected, Qt.Key.Key_R: self.reject_selected,
                    Qt.Key.Key_P: self.pending_selected}.get(event.key())
        if callback:
            callback()
            event.accept()
            return
        super().keyPressEvent(event)


__all__ = ["ReviewPage"]
