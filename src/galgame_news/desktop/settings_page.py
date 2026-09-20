"""Settings page for non-secret settings, credentials, theme, and FFmpeg."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..settings import (
    AppSettings,
    CredentialStore,
    InMemoryCredentialBackend,
    SettingsStore,
    discover_ffmpeg,
)


class SettingsPage(QWidget):
    theme_changed = Signal(str)
    ffmpeg_checked = Signal(object)

    def __init__(
        self,
        settings_store: SettingsStore | None = None,
        credential_store: CredentialStore | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self.settings_store = settings_store or SettingsStore()
        if credential_store is None:
            try:
                credential_store = CredentialStore()
            except RuntimeError:
                credential_store = CredentialStore(InMemoryCredentialBackend())
        self.credential_store = credential_store
        self._settings = self._load_settings()

        self.output_edit = QLineEdit(str(self._settings.default_output_path))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["System", "Light", "Dark"])
        self.theme_combo.setCurrentText(self._settings.theme.title())
        self.brave_edit = QLineEdit()
        self.brave_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.x_edit = QLineEdit()
        self.x_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.save)
        self.ffmpeg_button = QPushButton("Diagnose FFmpeg")
        self.ffmpeg_button.clicked.connect(self.diagnose_ffmpeg)
        self.ffmpeg_status = QLabel("Not checked")

        common = QGroupBox("Common settings")
        common_form = QFormLayout(common)
        common_form.addRow("Default output", self.output_edit)
        common_form.addRow("Theme", self.theme_combo)
        secrets = QGroupBox("Credentials (Windows Credential Manager/keyring)")
        secret_form = QFormLayout(secrets)
        secret_form.addRow("Brave API key", self.brave_edit)
        secret_form.addRow("X bearer token", self.x_edit)
        ffmpeg = QGroupBox("FFmpeg")
        ffmpeg_layout = QHBoxLayout(ffmpeg)
        ffmpeg_layout.addWidget(self.ffmpeg_button)
        ffmpeg_layout.addWidget(self.ffmpeg_status, 1)
        layout = QVBoxLayout(self)
        layout.addWidget(common)
        layout.addWidget(secrets)
        layout.addWidget(ffmpeg)
        layout.addWidget(self.save_button)
        layout.addStretch(1)

    def _load_settings(self) -> AppSettings:
        try:
            return self.settings_store.load()
        except ValueError:
            return AppSettings()

    @staticmethod
    def _theme_value(text: str) -> str:
        return {"System": "system", "Light": "light", "Dark": "dark"}.get(text, "system")

    def save(self) -> AppSettings:
        theme = self._theme_value(self.theme_combo.currentText())
        values = self._settings.model_dump()
        values.update({"default_output_path": Path(self.output_edit.text()), "theme": theme})
        settings = AppSettings.model_validate(values)
        self.settings_store.save(settings)
        self._settings = settings
        if self.brave_edit.text():
            self.credential_store.set("brave_api_key", self.brave_edit.text())
        if self.x_edit.text():
            self.credential_store.set("x_bearer_token", self.x_edit.text())
        self.theme_changed.emit(theme)
        return settings

    def diagnose_ffmpeg(self):
        settings = self._settings
        status = discover_ffmpeg(
            configured_location=settings.ffmpeg_location,
            configured_probe_location=settings.ffprobe_location,
        )
        self.ffmpeg_status.setText(status.diagnostic)
        self.ffmpeg_checked.emit(status)
        return status


__all__ = ["SettingsPage"]
