from __future__ import annotations

import json
from pathlib import Path

import pytest

from galgame_news.settings import AppSettings, SettingsStore, default_app_data_dir


def test_settings_are_typed_and_persisted_without_secret_fields(tmp_path: Path):
    settings_path = tmp_path / "GalgameNewsToolbox" / "settings.json"
    expected_output = tmp_path / "exports"
    settings = AppSettings(
        default_output_path=expected_output,
        max_image_bytes=2_000_000,
        max_video_bytes=80_000_000,
        max_video_duration_seconds=90,
        theme="dark",
    )

    SettingsStore(settings_path).save(settings)
    loaded = SettingsStore(settings_path).load()

    assert loaded.default_output_path == expected_output
    assert loaded.max_image_bytes == 2_000_000
    assert loaded.max_video_bytes == 80_000_000
    assert loaded.max_video_duration_seconds == 90
    assert loaded.theme == "dark"
    payload = settings_path.read_text(encoding="utf-8")
    assert "brave" not in payload.casefold()
    assert "token" not in payload.casefold()


def test_settings_reject_unknown_values_and_invalid_limits():
    with pytest.raises(ValueError):
        AppSettings(theme="neon")
    with pytest.raises(ValueError):
        AppSettings(max_image_bytes=0)


def test_default_app_data_dir_follows_windows_local_appdata():
    path = default_app_data_dir({"LOCALAPPDATA": r"C:\Users\tester\AppData\Local"})
    assert path == Path(r"C:\Users\tester\AppData\Local") / "GalgameNewsToolbox"


def test_settings_store_rejects_secret_bearing_payload(tmp_path: Path):
    path = tmp_path / "settings.json"
    with pytest.raises(ValueError):
        SettingsStore(path).save({"brave_api_key": "do-not-write"})
    assert not path.exists()


def test_settings_store_rejects_subclass_with_secret_fields(tmp_path: Path):
    class SecretSettings(AppSettings):
        brave_api_key: str = "secret-must-not-be-written"

    path = tmp_path / "settings.json"
    with pytest.raises(ValueError, match="exact AppSettings"):
        SettingsStore(path).save(SecretSettings())
    assert not path.exists()


def test_settings_file_is_json_and_does_not_contain_pydantic_metadata(tmp_path: Path):
    path = tmp_path / "settings.json"
    SettingsStore(path).save(AppSettings())
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["theme"] == "system"
    assert "model_config" not in payload


def test_settings_store_read_write_aliases_and_path_aliases(tmp_path: Path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.write(AppSettings(image_max_bytes=7))
    assert store.read().image_max_bytes == 7
    assert AppSettings(ffmpeg_location=tmp_path / "ffmpeg").ffmpeg_path == tmp_path / "ffmpeg"
    assert AppSettings(ffmpeg_path=tmp_path / "ffmpeg").ffmpeg_location == tmp_path / "ffmpeg"
    assert AppSettings(ffprobe_path=tmp_path / "ffprobe").ffprobe_location == tmp_path / "ffprobe"
