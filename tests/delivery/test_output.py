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
