from __future__ import annotations

from galgame_news.video.downloader import resolve_ffmpeg_location


def _install_fake_version(root, package_name: str, build_name: str):
    directory = root / "Microsoft" / "WinGet" / "Packages" / package_name / build_name / "bin"
    directory.mkdir(parents=True)
    (directory / "ffmpeg.exe").write_bytes(b"ffmpeg")
    (directory / "ffprobe.exe").write_bytes(b"ffprobe")
    return directory


def test_winget_nested_builds_are_found_and_selected_deterministically(tmp_path):
    local_appdata = tmp_path / "local"
    old = _install_fake_version(local_appdata, "Gyan.FFmpeg_8", "ffmpeg-8.0.1-full_build")
    new = _install_fake_version(local_appdata, "Gyan.FFmpeg_9", "ffmpeg-9.0.1-full_build")

    resolution = resolve_ffmpeg_location(
        environ={"LOCALAPPDATA": str(local_appdata)},
        which=lambda _name: None,
        repo_root=tmp_path,
    )

    assert resolution.available is True
    assert resolution.source == "winget"
    assert resolution.location == str(new)
    assert resolution.location != str(old)
    assert resolution.ffmpeg_path == str(new / "ffmpeg.exe")
    assert resolution.ffprobe_path == str(new / "ffprobe.exe")
