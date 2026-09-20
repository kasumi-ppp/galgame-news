from __future__ import annotations

from pathlib import Path

from galgame_news.desktop.history_page import HistoryPage
from galgame_news.tasks import TaskStore


def test_history_removal_forgets_catalog_only(qtbot, tmp_path: Path):
    source = tmp_path / "input.docx"
    source.write_bytes(b"docx")
    store = TaskStore(tmp_path / "app")
    record = store.create_task("issue", source, task_root=tmp_path / "managed")
    page = HistoryPage(store)
    qtbot.addWidget(page)
    page.records = [record]
    page.task_list.clear()
    page.task_list.addItem(record.task_id)
    page.task_list.setCurrentRow(0)
    assert page.remove_selected() is True
    assert (tmp_path / "managed").is_dir()
    assert not store.get_task(record.task_id)
