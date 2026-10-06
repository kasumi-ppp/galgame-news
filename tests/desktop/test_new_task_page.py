from pathlib import Path

from PySide6.QtWidgets import QApplication

from galgame_news.desktop.new_task_page import NewTaskPage


def test_socialdata_is_opt_in_per_task_and_included_in_start_request(tmp_path):
    app = QApplication.instance() or QApplication([])
    page = NewTaskPage(default_output=tmp_path / "output")
    page.set_input_path(tmp_path / "261.docx")
    payloads = []
    page.start_requested.connect(payloads.append)

    assert page.socialdata_checkbox.isChecked() is False
    page.socialdata_checkbox.setChecked(True)
    page.request_start()

    assert payloads[0]["use_socialdata_x"] is True
    page.close()
    app.processEvents()

