from __future__ import annotations

import json
from pathlib import Path

from galgame_news.domain import Issue, NewsItem, PipelineResult, VideoCandidate, VideoStatus


def test_output_writes_video_index_and_places_downloaded_video_under_news_image_dir(tmp_path):
    from galgame_news.delivery.output import OutputManager

    item = NewsItem(issue_id="1", sequence=1, section="新作", title="Video", body="")
    staged = tmp_path / "stage.mp4"
    staged.write_bytes(b"video")
    video = VideoCandidate(
        news_id=item.id,
        source_url="https://official.example/news",
        video_url="https://youtube.com/watch?v=abc",
        title="Trailer:<>",
        local_path=str(staged),
        downloadable=True,
        status=VideoStatus.DOWNLOADED,
        duration_seconds=12,
        width=1920,
        height=1080,
        format="mp4",
        byte_size=5,
        sha256="hash",
    )

    out = tmp_path / "out"
    OutputManager().write(
        PipelineResult(
            issue=Issue(issue_id="1", input_path="fixture.docx", news_items=[item]),
            videos=[video],
        ),
        out,
    )

    target = out / "images" / "x1" / "Trailer.mp4"
    assert target.read_bytes() == b"video"
    payload = json.loads((out / "video_index.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["news_items"][0]["news_id"] == item.id
    assert payload["videos"][0]["status"] == "downloaded"
    assert payload["videos"][0]["local_path"].replace("\\", "/").endswith("images/x1/Trailer.mp4")


def test_output_writes_empty_video_index_for_no_videos(tmp_path):
    from galgame_news.delivery.output import OutputManager

    out = tmp_path / "out"
    OutputManager().write(
        PipelineResult(issue=Issue(issue_id="1", input_path="fixture.docx", news_items=[])),
        out,
    )
    payload = json.loads((out / "video_index.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["videos"] == []
