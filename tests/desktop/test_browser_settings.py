from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from galgame_news.desktop import settings_page as settings_page_module
from galgame_news.desktop.settings_page import SettingsPage
from galgame_news.settings import AppSettings, InMemoryCredentialBackend, CredentialStore, SettingsStore
from galgame_news.settings.browser import PLAYWRIGHT_VERSION, browser_install_commands


class FakeSignal:
    def __init__(self):
        self.callback = None

    def connect(self, callback):
        self.callback = callback

    def emit(self, *args):
        if self.callback:
            self.callback(*args)


class FakeProcess:
    class ProcessState:
        NotRunning = 0

    class ExitStatus:
        NormalExit = 0

    class ProcessError:
        FailedToStart = 0

    def __init__(self, _parent=None):
        self.finished = FakeSignal()
        self.errorOccurred = FakeSignal()
        self.commands = []

    def state(self):
        return self.ProcessState.NotRunning

    def start(self, program, arguments):
        self.commands.append((program, tuple(arguments)))


def test_browser_setting_is_saved_and_installer_runs_pinned_steps_without_shell(tmp_path: Path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings_page_module, "QProcess", FakeProcess)
    store = SettingsStore(app_data=tmp_path / "app")
    credentials = CredentialStore(InMemoryCredentialBackend())
    page = SettingsPage(settings_store=store, credential_store=credentials)
    page.browser_enabled_checkbox.setChecked(False)
    saved = page.save()
    assert saved.browser_enabled is False
    assert store.load().browser_enabled is False

    monkeypatch.setattr(
        settings_page_module,
        "check_browser_runtime",
        lambda: SimpleNamespace(available=True, message="已就绪"),
    )
    page.install_browser()
    expected = browser_install_commands()
    assert page._browser_process.commands == [expected[0]]
    page._browser_process.finished.emit(0, FakeProcess.ExitStatus.NormalExit)
    assert page._browser_process.commands == list(expected)
    page._browser_process.finished.emit(0, FakeProcess.ExitStatus.NormalExit)
    assert page.browser_status.text() == "已就绪"
    assert page.browser_check_button.isEnabled()
    assert PLAYWRIGHT_VERSION in expected[0][1][-1]
    page.close()
    app.processEvents()


def test_browser_enabled_defaults_on_for_existing_settings():
    assert AppSettings().browser_enabled is True

