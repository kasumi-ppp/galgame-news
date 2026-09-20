from __future__ import annotations

from pathlib import Path

from galgame_news.desktop.settings_page import SettingsPage
from galgame_news.desktop.thumbnail_cache import ThumbnailCache
from galgame_news.settings import AppSettings, InMemoryCredentialBackend, CredentialStore, SettingsStore


def test_thumbnail_cache_is_lazy_and_bounded(tmp_path: Path):
    cache = ThumbnailCache(max_items=2)
    calls: list[str] = []

    def load(path: str) -> object:
        calls.append(path)
        return object()

    assert cache.get("a", lambda: load("a")) is not None
    assert cache.get("a", lambda: load("a")) is not None
    cache.get("b", lambda: load("b"))
    cache.get("c", lambda: load("c"))
    assert calls == ["a", "b", "c"]
    assert len(cache) == 2
    assert cache.contains("a") is False


def test_settings_page_persists_theme_and_credential(tmp_path: Path, qtbot):
    store = SettingsStore(app_data=tmp_path / "app")
    credentials = CredentialStore(InMemoryCredentialBackend())
    page = SettingsPage(settings_store=store, credential_store=credentials)
    qtbot.addWidget(page)
    page.theme_combo.setCurrentText("Dark")
    page.brave_edit.setText("secret")
    page.save()
    assert store.load().theme == "dark"
    assert credentials.get("brave_api_key") == "secret"
