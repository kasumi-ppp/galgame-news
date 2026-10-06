from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from galgame_news.desktop.controller import DesktopController


def test_socialdata_task_choice_survives_controller_resume(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    input_path = tmp_path / "261.docx"
    input_path.write_bytes(b"fixture")
    output = tmp_path / "output"
    task_dir = output / "261-task"
    requests = []

    class Runner:
        def run(self, request, event_sink=None, cancellation_token=None):
            requests.append(request)
            return SimpleNamespace(status="completed", task_dir=task_dir)

    controller = DesktopController(runner_factory=Runner, app_data=tmp_path / "app")

    def run_and_wait(start):
        loop = QEventLoop()
        controller.task_finished.connect(loop.quit)
        QTimer.singleShot(5000, loop.quit)
        assert start() is True
        loop.exec()
        assert controller.wait_for_task(5000) is True
        app.processEvents()

    try:
        run_and_wait(lambda: controller.start_task(
            input_path=input_path, issue_id="261", output_dir=output,
            use_socialdata_x=True,
        ))
        record = controller.task_store.active_task
        assert record is not None

        run_and_wait(lambda: controller.resume_task(record))

        assert requests[0].use_socialdata_x is True
        assert requests[1].use_socialdata_x is True
    finally:
        controller.task_store.close()
        controller.deleteLater()
        app.processEvents()
