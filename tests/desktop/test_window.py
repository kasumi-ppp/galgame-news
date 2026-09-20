from __future__ import annotations

from pathlib import Path
from threading import Event
from types import SimpleNamespace

from galgame_news.desktop.controller import DesktopController
from galgame_news.desktop.window import MainWindow


def test_main_window_has_five_pages_and_switches_navigation(qtbot, tmp_path: Path):
    controller = DesktopController(app_data=tmp_path / "app")
    window = MainWindow(controller)
    qtbot.addWidget(window)
    assert window.navigation.count() == 5
    assert window.nav_list is window.navigation
    assert window.stacked_widget is window.pages
    assert [window.navigation.item(i).text() for i in range(5)] == [
        "New Task",
        "Progress",
        "Review",
        "History",
        "Settings",
    ]
    window.navigation.setCurrentRow(2)
    assert window.pages.currentWidget() is window.review_page


def test_window_close_requests_cooperative_stop(qtbot, tmp_path: Path):
    controller = DesktopController(app_data=tmp_path / "app")
    window = MainWindow(controller)
    qtbot.addWidget(window)
    called: list[bool] = []
    controller.stop_requested.connect(lambda: called.append(True))
    window.close()
    assert called == []


def test_window_defers_close_until_running_worker_finishes(qtbot, tmp_path: Path):
    entered = Event()
    release = Event()

    class _DelayedRunner:
        def run(self, request, event_sink=None, cancellation_token=None):
            entered.set()
            release.wait(5)
            return SimpleNamespace(status="completed", task_dir=tmp_path / "task")

    controller = DesktopController(
        runner_factory=_DelayedRunner,
        app_data=tmp_path / "app",
    )
    window = MainWindow(controller)
    qtbot.addWidget(window)
    assert controller.start_task(
        input_path=tmp_path / "issue.docx",
        issue_id="issue",
        output_dir=tmp_path / "output",
    ) is True
    assert entered.wait(2)

    window.show()
    assert window.close() is False
    assert controller.is_running is True

    release.set()
    qtbot.waitUntil(lambda: not controller.is_running, timeout=3000)
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=3000)
