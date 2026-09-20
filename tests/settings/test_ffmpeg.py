from __future__ import annotations

from pathlib import Path

from galgame_news.settings import discover_ffmpeg
from galgame_news.settings.ffmpeg import resolve_ffmpeg, resolve_ffmpeg_location


def _install(directory: Path, *, probe: bool = True) -> None:
    directory.mkdir(parents=True)
    (directory / "ffmpeg.exe").write_bytes(b"ffmpeg")
    if probe:
        (directory / "ffprobe.exe").write_bytes(b"ffprobe")


def test_bundled_bin_is_preferred_to_configured_and_system(tmp_path: Path):
    bundled = tmp_path / "bundle" / "bin"
    configured = tmp_path / "configured"
    system = tmp_path / "system" / "ffmpeg.exe"
    _install(bundled)
    _install(configured)
    system.parent.mkdir()
    system.write_bytes(b"ffmpeg")

    status = discover_ffmpeg(
        bundle_dir=tmp_path / "bundle",
        configured_location=str(configured),
        which=lambda name: str(system) if name == "ffmpeg" else None,
    )

    assert status.available is True
    assert status.source == "bundled"
    assert status.location == str(bundled.resolve())
    assert status.ffprobe_path == str((bundled / "ffprobe.exe").resolve())


def test_configured_location_is_used_when_bundle_is_missing(tmp_path: Path):
    configured = tmp_path / "configured"
    _install(configured)
    status = discover_ffmpeg(
        bundle_dir=tmp_path / "bundle",
        configured_location=str(configured),
        which=lambda _name: None,
    )
    assert status.available is True
    assert status.source == "configured"


def test_system_location_and_missing_probe_are_reported(tmp_path: Path):
    ffmpeg = tmp_path / "system" / "ffmpeg.exe"
    ffmpeg.parent.mkdir()
    ffmpeg.write_bytes(b"ffmpeg")
    status = discover_ffmpeg(
        bundle_dir=tmp_path / "bundle",
        which=lambda name: str(ffmpeg) if name == "ffmpeg" else None,
    )
    assert status.available is True
    assert status.source == "system"
    assert status.ffmpeg_path == str(ffmpeg.resolve())
    assert status.ffprobe_path is None
    assert "ffprobe" in status.diagnostic.casefold()


def test_missing_ffmpeg_exposes_diagnostic_status(tmp_path: Path):
    status = discover_ffmpeg(bundle_dir=tmp_path / "bundle", which=lambda _name: None)
    assert status.available is False
    assert status.source == "none"
    assert status.error
    assert "not found" in status.diagnostic.casefold()


def test_bundle_bin_path_is_accepted_and_system_probe_can_be_separate(tmp_path: Path):
    bundled_bin = tmp_path / "bundle" / "bin"
    _install(bundled_bin, probe=False)
    system_probe = tmp_path / "system" / "ffprobe.exe"
    system_probe.parent.mkdir()
    system_probe.write_bytes(b"ffprobe")

    def which(name: str) -> str | None:
        if name == "ffmpeg":
            return str(bundled_bin / "ffmpeg.exe")
        if name == "ffprobe":
            return str(system_probe)
        return None

    status = discover_ffmpeg(bundle_dir=bundled_bin, which=which)

    assert status.source == "bundled"
    assert status.ffprobe_path == str(system_probe.resolve())


def test_resolution_aliases_keep_existing_calling_conventions(tmp_path: Path):
    directory = tmp_path / "configured"
    _install(directory)
    assert resolve_ffmpeg(str(directory), bundle_dir=tmp_path / "missing").available is True
    assert resolve_ffmpeg_location(str(directory), bundle_dir=tmp_path / "missing").available is True


def test_frozen_executable_directory_bin_is_a_bundled_candidate(tmp_path: Path, monkeypatch):
    executable_root = tmp_path / "portable"
    bundled = executable_root / "bin"
    _install(bundled)
    monkeypatch.setattr("galgame_news.settings.ffmpeg.sys.frozen", True, raising=False)
    monkeypatch.setattr(
        "galgame_news.settings.ffmpeg.sys.executable",
        str(executable_root / "GalgameNewsToolbox.exe"),
        raising=False,
    )

    status = discover_ffmpeg(which=lambda _name: None)

    assert status.available is True
    assert status.source == "bundled"
    assert status.location == str(bundled.resolve())
