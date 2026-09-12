from datetime import datetime, timezone
import json

from galgame_news.domain import ImageCandidate, ImageNeed, ImageNeed, Issue, NewsItem, PipelineResult, SourceType


def test_output_manager_writes_indexes_and_news_mapping(tmp_path):
    from galgame_news.delivery.output import OutputManager

    item = NewsItem(issue_id="259", sequence=1, section="新作", title="《Game》更新", body="", image_need=ImageNeed.UNKNOWN)
    issue = Issue(issue_id="259", input_path="fixture.docx", news_items=[item])
    candidate = ImageCandidate(news_id=item.id, image_url="https://cdn.example/a.jpg", source_url="https://official.example", source_type=SourceType.OFFICIAL_SITE, fetched_at=datetime.now(timezone.utc), selected=True)
    manifest = OutputManager().write(PipelineResult(issue=issue, candidates=[candidate]), tmp_path)
    assert manifest.image_count == 1
    for name in ("image_index.json", "image_index.md", "failed_items.json", "review_required.json"):
        assert (tmp_path / name).exists()
    payload = json.loads((tmp_path / "image_index.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["news_items"][0]["news_id"] == item.id


def test_output_is_repeatable_and_does_not_duplicate_candidate_files(tmp_path):
    from galgame_news.delivery.output import OutputManager

    issue = Issue(issue_id="1", input_path="fixture.docx", news_items=[])
    result = PipelineResult(issue=issue)
    manager = OutputManager()
    manager.write(result, tmp_path)
    manager.write(result, tmp_path)
    assert len(list(tmp_path.glob("*.json"))) == 3


def test_output_uses_human_readable_section_sequence_names(tmp_path):
    from galgame_news.delivery.output import OutputManager

    item = NewsItem(issue_id="1", sequence=2, section="新作", title="《Game》更新", body="", image_need=ImageNeed.UNKNOWN)
    issue = Issue(issue_id="1", input_path="fixture.docx", news_items=[item])
    source = tmp_path / "source.jpg"
    source.write_bytes(b"sample")
    candidate = ImageCandidate(news_id=item.id, image_url="https://cdn.example/a.jpg", source_url="https://official.example", source_type=SourceType.OFFICIAL_SITE, fetched_at=datetime.now(timezone.utc), local_path=str(source), selected=True)
    OutputManager().write(PipelineResult(issue=issue, candidates=[candidate]), tmp_path / "out")
    assert (tmp_path / "out" / "images" / "新作2" / "01.jpg").exists()


def test_output_rerun_removes_managed_images_that_are_no_longer_present(tmp_path):
    from galgame_news.delivery.output import OutputManager
    item = NewsItem(issue_id="1", sequence=1, section="新作", title="Game", body="", image_need=ImageNeed.UNKNOWN)
    issue = Issue(issue_id="1", input_path="fixture.docx", news_items=[item])
    source = tmp_path / "source.jpg"; source.write_bytes(b"sample")
    candidates = [ImageCandidate(news_id=item.id, image_url=f"https://cdn.example/{i}.jpg", source_url="https://official.example", source_type=SourceType.OFFICIAL_SITE, fetched_at=datetime.now(timezone.utc), local_path=str(source), selected=True) for i in range(10)]
    manager = OutputManager(); out = tmp_path / "out"
    manager.write(PipelineResult(issue=issue, candidates=candidates), out)
    assert len(list((out / "images" / "新作1").glob("*.jpg"))) == 10
    source2 = tmp_path / "source2.jpg"; source2.write_bytes(b"sample2")
    second = [ImageCandidate(news_id=item.id, image_url=f"https://cdn.example/second-{i}.jpg", source_url="https://official.example", source_type=SourceType.OFFICIAL_SITE, fetched_at=datetime.now(timezone.utc), local_path=str(source2), selected=True) for i in range(3)]
    manager.write(PipelineResult(issue=issue, candidates=second), out)
    assert sorted(p.name for p in (out / "images" / "新作1").glob("*.jpg")) == ["01.jpg", "02.jpg", "03.jpg"]
