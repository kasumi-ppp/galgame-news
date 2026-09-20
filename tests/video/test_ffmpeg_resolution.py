from __future__ import annotations

from pathlib import Path

from galgame_news.config import VideoConfig
from galgame_news.domain import VideoCandidate
from galgame_news.video.downloader import VideoDownloader, resolve_ffmpeg_location


class _Backend:
    def __init__(self, options):
        self.options = options
        self.info = {
            "title": "merge test",
            "duration": 10,
            "width": 1280,
            "height": 720,
            "ext": "mp4",
            "filesize": 4,
        }

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def extract_info(self, _url, download=False):
        assert download is False
        return dict(self.info)

    def download(self, _urls):
        output = Path(str(self.options["outtmpl"]).replace("%(ext)s", "mp4"))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"data")


def _candidate() -> VideoCandidate:
    return VideoCandidate(
        news_id="x1",
        source_url="https://news.example/item",
        video_url="https://video.example/watch?v=1",
    )


def test_explicit_location_is_resolved_and_passed_to_yt_dlp(tmp_path):
    bin_dir = tmp_path / "ffmpeg" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "ffmpeg.exe").write_bytes(b"ffmpeg")
    (bin_dir / "ffprobe.exe").write_bytes(b"ffprobe")

    resolution = resolve_ffmpeg_location(
        config_location=str(bin_dir),
        environ={},
        which=lambda _name: None,
        repo_root=tmp_path,
    )
    assert resolution.available is True
    assert resolution.location == str(bin_dir)
    assert resolution.ffmpeg_path == str(bin_dir / "ffmpeg.exe")
    assert resolution.ffprobe_path == str(bin_dir / "ffprobe.exe")

    calls = []

    def factory(options):
        calls.append(options)
        return _Backend(options)

    result, failures = VideoDownloader(
        VideoConfig(ffmpeg_location=str(bin_dir)),
        backend_factory=factory,
    ).download([_candidate()], tmp_path / "out")

    assert failures == []
    assert result[0].status.value == "downloaded"
    assert calls[0]["ffmpeg_location"] == str(bin_dir)
    assert "bv*[height<=1080]+ba" in calls[0]["format"]


def test_bundled_bin_is_preferred_by_downloader_before_configured_location(tmp_path):
    bundled_root = tmp_path / "bundle"
    bundled_dir = bundled_root / "bin"
    configured_dir = tmp_path / "configured" / "bin"
    for directory in (bundled_dir, configured_dir):
        directory.mkdir(parents=True)
        (directory / "ffmpeg.exe").write_bytes(b"ffmpeg")
        (directory / "ffprobe.exe").write_bytes(b"ffprobe")

    resolution = resolve_ffmpeg_location(
        config_location=str(configured_dir),
        environ={},
        which=lambda _name: None,
        repo_root=bundled_root,
    )

    assert resolution.available is True
    assert resolution.source == "bundled"
    assert resolution.location == str(bundled_dir.resolve())


def test_video_downloader_uses_bundled_resolution_at_runtime(tmp_path, monkeypatch):
    bundle_root = tmp_path / "bundle"
    bundled_dir = bundle_root / "bin"
    configured_dir = tmp_path / "configured" / "bin"
    for directory in (bundled_dir, configured_dir):
        directory.mkdir(parents=True)
        (directory / "ffmpeg.exe").write_bytes(b"ffmpeg")
        (directory / "ffprobe.exe").write_bytes(b"ffprobe")
    monkeypatch.setattr("galgame_news.settings.ffmpeg.sys._MEIPASS", str(bundle_root), raising=False)

    downloader = VideoDownloader(VideoConfig(ffmpeg_location=str(configured_dir)))
    available, location = downloader._ffmpeg_state()

    assert available is True
    assert location == str(bundled_dir.resolve())
    assert downloader.ffmpeg_resolution is not None
    assert downloader.ffmpeg_resolution.source == "bundled"


def test_environment_location_takes_precedence_over_config(tmp_path):
    env_dir = tmp_path / "env" / "bin"
    config_dir = tmp_path / "config" / "bin"
    for directory in (env_dir, config_dir):
        directory.mkdir(parents=True)
        (directory / "ffmpeg.exe").write_bytes(b"ffmpeg")

    resolution = resolve_ffmpeg_location(
        config_location=str(config_dir),
        environ={"FFMPEG_LOCATION": str(env_dir)},
        which=lambda _name: None,
        repo_root=tmp_path,
    )

    assert resolution.available is True
    assert resolution.location == str(env_dir)
    assert resolution.source == "environment"


def test_path_and_winget_portable_fallbacks_are_supported(tmp_path):
    path_dir = tmp_path / "path" / "bin"
    path_dir.mkdir(parents=True)
    path_ffmpeg = path_dir / "ffmpeg.exe"
    path_ffmpeg.write_bytes(b"ffmpeg")
    path_resolution = resolve_ffmpeg_location(
        environ={},
        which=lambda name: str(path_ffmpeg) if name == "ffmpeg" else None,
        repo_root=tmp_path,
    )
    assert path_resolution.available is True
    assert path_resolution.source == "path"

    winget_dir = tmp_path / "local" / "Microsoft" / "WinGet" / "Packages" / "Gyan.FFmpeg_test" / "bin"
    winget_dir.mkdir(parents=True)
    (winget_dir / "ffmpeg.exe").write_bytes(b"ffmpeg")
    (winget_dir / "ffprobe.exe").write_bytes(b"ffprobe")
    winget_resolution = resolve_ffmpeg_location(
        environ={"LOCALAPPDATA": str(tmp_path / "local")},
        which=lambda _name: None,
        repo_root=tmp_path,
    )
    assert winget_resolution.available is True
    assert winget_resolution.source == "winget"
    assert winget_resolution.location == str(winget_dir)

    portable_dir = tmp_path / ".tools" / "ffmpeg" / "bin"
    portable_dir.mkdir(parents=True)
    (portable_dir / "ffmpeg.exe").write_bytes(b"ffmpeg")
    portable_resolution = resolve_ffmpeg_location(
        environ={"LOCALAPPDATA": str(tmp_path / "empty-local")},
        which=lambda _name: None,
        repo_root=tmp_path,
    )
    assert portable_resolution.available is True
    assert portable_resolution.source == "portable"


def test_invalid_explicit_location_is_reported_without_claiming_ffmpeg(tmp_path):
    resolution = resolve_ffmpeg_location(
        config_location=str(tmp_path / "missing"),
        environ={},
        which=lambda _name: None,
        repo_root=tmp_path,
    )

    assert resolution.available is False
    assert resolution.error is not None
    assert "invalid explicit" in resolution.error
