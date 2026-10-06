from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from galgame_news.domain import ImageCandidate, Issue, NewsItem, PipelineResult, ReviewReason, VideoCandidate, VideoStatus
from galgame_news.pipeline import PipelineRunner
from galgame_news.pipeline import runner as runner_module


def _result(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (60, 40), "red").save(source)
    news = NewsItem(issue_id="262", sequence=1, section="新作", title="Game", body="CG")
    image = ImageCandidate(
        news_id=news.id, image_url="https://example.test/cg.png",
        source_url="https://example.test/game", fetched_at=datetime.now(timezone.utc),
        local_path=str(source), downloadable=True, selected=True,
        review_reasons=[ReviewReason.UNCERTAIN_MATCH],
    )
    filtered = image.model_copy(deep=True, update={"image_url": "https://example.test/extra.png", "selected": False})
    object.__setattr__(filtered, "id", "extra")
    video_source = tmp_path / "source.mp4"
    video_source.write_bytes(b"fixture video")
    video = VideoCandidate(
        news_id=news.id, source_url="https://example.test/game",
        video_url="https://example.test/pv.mp4", local_path=str(video_source),
        status=VideoStatus.DOWNLOADED,
    )
    return PipelineResult(
        issue=Issue(issue_id="262", input_path="fixture.docx", news_items=[news]),
        candidates=[image], filtered_candidates=[filtered],
        review_required=[image, filtered], videos=[video],
    )


def test_publish_serializes_final_image_original_video_and_review_paths(tmp_path):
    task = tmp_path / "task"
    raw = task / "raw"
    result = _result(tmp_path)

    manifest = PipelineRunner()._publish_output(result, raw, task)

    assert manifest.output_dir == str(raw)
    assert all((raw / name).is_file() for name in manifest.files)
    for filename, key in (("image_index.json", "candidates"), ("video_index.json", "videos"), ("review_required.json", None)):
        payload = json.loads((raw / filename).read_text(encoding="utf-8"))
        records = payload[key] if key else payload
        assert ".work" not in json.dumps(payload)
        for record in records:
            for field in ("local_path", "original_path"):
                if record.get(field):
                    path = Path(record[field])
                    assert path.is_file()
                    path.relative_to(raw)
    for image in result.all_candidates:
        assert Path(image.local_path).is_file()
        assert Path(image.original_path).is_file()


@pytest.mark.parametrize("failure", ["index", "rename"])
def test_failed_publish_preserves_previous_raw_and_in_memory_paths(tmp_path, monkeypatch, failure):
    task = tmp_path / "task"
    raw = task / "raw"
    raw.mkdir(parents=True)
    (raw / "previous.txt").write_text("previous", encoding="utf-8")
    result = _result(tmp_path)
    before = result.model_dump(mode="json")
    if failure == "index":
        def broken_write(*args, **kwargs):
            raise OSError("index rewrite failed")
        monkeypatch.setattr(runner_module, "atomic_json_write", broken_write)
    else:
        original_replace = runner_module.os.replace
        def broken_replace(source, target):
            if Path(source).name.startswith("publish-"):
                raise OSError("publish rename failed")
            return original_replace(source, target)
        monkeypatch.setattr(runner_module.os, "replace", broken_replace)

    with pytest.raises(OSError):
        PipelineRunner()._publish_output(result, raw, task)

    assert (raw / "previous.txt").read_text(encoding="utf-8") == "previous"
    assert result.model_dump(mode="json") == before
    assert not list(task.glob(".raw-backup-*"))
    assert not list((task / ".work").glob("publish-*"))


def test_rewrite_covers_nested_models_dicts_and_manifest_without_substring_replacement(tmp_path):
    stage = tmp_path / ".work" / "publish-a"
    final = tmp_path / "raw"
    payload = {
        "result": {"candidates": [{"local_path": str(stage / "images/a.png"), "original_path": str(stage / "originals/a.png")} ]},
        "manifest": {"output_dir": str(stage), "files": [str(stage / "images/a.png"), "images/a.png"]},
        "unrelated": str(stage) + "-other/images/a.png",
    }

    PipelineRunner._rewrite_paths(payload, stage, final)

    assert payload["result"]["candidates"][0]["original_path"] == str(final / "originals/a.png")
    assert payload["manifest"] == {"output_dir": str(final), "files": [str(final / "images/a.png"), "images/a.png"]}
    assert payload["unrelated"] == str(stage) + "-other/images/a.png"
