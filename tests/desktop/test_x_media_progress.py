"""Offline checks for the structured X media progress snapshots."""

import pytest
from PySide6.QtWidgets import QApplication

from galgame_news.desktop.i18n import X_MEDIA_COUNTERS, event_summary
from galgame_news.desktop.progress_page import ProgressPage
from galgame_news.pipeline import ProgressEvent


def stats_event(**payload):
    return ProgressEvent("x_media_stats", "task", "261", payload=payload)


@pytest.fixture
def page():
    app = QApplication.instance() or QApplication([])
    widget = ProgressPage()
    yield widget
    widget.deleteLater()
    app.processEvents()


def test_x_media_stats_display_absolute_counts_and_recovery(page):
    snapshot = dict(x_queries=3, x_attachments=7, x_downloaded=4, x_pending=3, x_failed=2)
    page.handle_event(stats_event(**snapshot))
    page.handle_event(stats_event(**snapshot))
    assert page.x_media_counters == snapshot
    assert {key: label.text() for key, label in page.x_media_labels.items()} == {
        key: str(value) for key, value in snapshot.items()
    }
    page.handle_event(stats_event(x_downloaded=7, x_pending=0, x_failed=0))
    assert page.x_media_counters == dict(snapshot, x_downloaded=7, x_pending=0, x_failed=0)
    assert page.x_media_group.title() == "X 媒体进度"
    assert X_MEDIA_COUNTERS["x_pending"] == "待恢复"
    assert event_summary("x_media_stats") == "X 媒体进度已更新"


def test_x_media_stats_preserve_news_progress_pause_and_private_logs(page):
    page.set_running(True)
    page.handle_event(ProgressEvent("news_started", "task", "261", news_index=1,
                                    total_news=3, message="新闻标题"))
    page.handle_event(ProgressEvent("news_completed", "task", "261"))
    page.set_paused(True)
    before = (dict(page.counters), page.stage_label.text(), page.current_news_label.text(),
              page.log.toPlainText(), page.technical_log.toPlainText())
    page.handle_event(ProgressEvent(
        "x_media_stats", "task", "261", total_news=100,
        message="private-api-key raw API response",
        payload=dict(x_queries=2, x_attachments=4, x_downloaded=1, x_pending=3, x_failed=1,
                     completed=100, total_news=100, raw_response="private-api-key"),
    ))
    assert (dict(page.counters), page.stage_label.text(), page.current_news_label.text(),
            page.log.toPlainText(), page.technical_log.toPlainText()) == before
    assert page.progress_bar.maximum() == 3
    assert page.progress_bar.value() == 1
    assert page.status_label.text() == "已暂停"
    assert page.pause_button.text() == "继续"
    seen = []
    page.resume_requested.connect(lambda: seen.append("resume"))
    page.request_pause_or_resume()
    assert seen == ["resume"]
    page.set_paused(False)
    page.handle_event(ProgressEvent("news_completed", "task", "261"))
    assert page.completed_label.text() == "2"
    assert page.failed_label.text() == "0"


def test_x_media_stats_reset_to_zero_with_normal_progress(page):
    assert all(label.text() == "0" for label in page.x_media_labels.values())
    page.set_running(True)
    page.handle_event(stats_event(x_queries=3, x_attachments=7, x_downloaded=4,
                                 x_pending=3, x_failed=2))
    page.handle_event(ProgressEvent("news_completed", "task", "261", total_news=3))
    page.set_paused(True)
    page.reset()
    assert page.x_media_counters == {key: 0 for key in X_MEDIA_COUNTERS}
    assert all(label.text() == "0" for label in page.x_media_labels.values())
    assert page.counters == {"completed": 0, "total": 0, "failed": 0}
    assert page.log.toPlainText() == page.technical_log.toPlainText() == ""
    assert page.status_label.text() == "等待开始"
    assert page.pause_button.text() == "暂停"
    assert page.progress_bar.isHidden()


def test_x_media_stats_ignore_invalid_counts_and_clamp_negative_counts(page):
    page.handle_event(stats_event(x_queries=2, x_attachments=3, x_downloaded=1))
    page.handle_event(stats_event(x_queries="invalid", x_attachments=None,
                                 x_downloaded=float("inf"), x_pending=-2, x_failed="1"))
    assert page.x_media_counters == dict(x_queries=2, x_attachments=3, x_downloaded=1,
                                        x_pending=0, x_failed=1)
