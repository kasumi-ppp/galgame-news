from __future__ import annotations

from pathlib import Path

from galgame_news.config import VideoConfig
from galgame_news.domain import VideoCandidate
from galgame_news.video.downloader import VideoDownloader


class _Backend:
    def __init__(self, options, info, payload):
        self.options = options
        self.info = info
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def extract_info(self, _url, download=False):
        assert download is False
        return dict(self.info)

    def download(self, _urls):
        output = Path(str(self.options["outtmpl"]).replace("%(ext)s", "mp4"))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(self.payload)


def test_unknown_metadata_that_exceeds_post_download_limit_is_removed(tmp_path):
    candidate = VideoCandidate(
        news_id="x1",
        source_url="https://news.example/item",
        video_url="https://video.example/watch?v=1",
        title="video",
    )
    info = {
        "title": "video",
        "duration": None,
        "width": 1920,
        "height": 1080,
        "ext": "mp4",
        "filesize": None,
    }

    def factory(options):
        return _Backend(options, info, b"too-large")

    downloaded, failures = VideoDownloader(
        VideoConfig(max_file_bytes=4),
        backend_factory=factory,
        ffmpeg_detector=lambda: False,
    ).download([candidate], tmp_path)

    assert downloaded[0].status.value == "skipped"
    assert failures[0].code == "file_size_exceeded"
    assert downloaded[0].local_path is None
    assert not list(Path(tmp_path).glob("*.mp4"))
    assert not list(Path(tmp_path).glob("*.webm"))
