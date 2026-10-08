from __future__ import annotations

from datetime import datetime, timezone

from galgame_news.curation.curator import ImageCurator
from galgame_news.domain import ImageCandidate, ImageNeed, Issue, NewsItem, SourceType


def test_structured_socialdata_photo_from_linked_post_overrides_entity_and_size_gates():
    post_url = "https://x.com/studio/status/2100750400404041890"
    item = NewsItem(
        id="n1", issue_id="261", sequence=1, section="新作",
        title="Game Alpha", body="", game_names=["Game Alpha"],
        source_urls=[post_url], image_need=ImageNeed.EXPLICIT_NEW_IMAGE,
    )
    candidate = ImageCandidate(
        news_id=item.id,
        image_url="https://pbs.twimg.com/media/small.jpg?name=orig",
        source_url=post_url,
        source_type=SourceType.OFFICIAL_X,
        fetched_at=datetime.now(timezone.utc),
        width=24, height=24, downloadable=True,
        signals={"socialdata_photo": True, "page_title": "A completely different franchise"},
    )

    result = ImageCurator().curate(
        Issue(issue_id="261", input_path="261.docx", news_items=[item]),
        [candidate],
    )

    assert candidate in result.candidates
    assert candidate.selected is True
    assert candidate.signals["auto_select"] is True
    assert candidate.signals["source_linked"] is True
    assert candidate.signals["entity_match"] == "unknown"


def test_invalid_x_photo_cannot_use_priority_to_auto_select():
    post = "https://x.com/studio/status/123"
    item = NewsItem(issue_id="1", sequence=1, section="新作", title="Game Alpha", body="", source_urls=[post])
    candidate = ImageCandidate(
        news_id=item.id, image_url="https://pbs.twimg.com/media/cg.jpg", source_url=post,
        source_type=SourceType.OFFICIAL_X, fetched_at=datetime.now(timezone.utc),
        width=1280, height=720, signals={"x_api_photo": True, "invalid_reason": "invalid_image"},
    )
    ImageCurator().curate(Issue(issue_id="1", input_path="fixture", news_items=[item]), [candidate])
    assert candidate.selected is False
    assert candidate.signals["auto_select"] is False


def test_socialdata_photo_can_be_selected_when_post_and_image_prove_current_cg():
    post_url = "https://x.com/studio/status/2100750400404041890"
    item = NewsItem(
        issue_id="261", sequence=1, section="新作", title="Game Alpha CG更新",
        body="官网公开新事件 CG", game_names=["Game Alpha"], source_urls=[post_url],
        image_need=ImageNeed.EXPLICIT_NEW_IMAGE,
    )
    candidate = ImageCandidate(
        news_id=item.id, image_url="https://pbs.twimg.com/media/capture.jpg?name=orig",
        source_url=post_url, source_type=SourceType.OFFICIAL_X,
        fetched_at=datetime.now(timezone.utc), width=1280, height=720,
        image_alt="Game Alpha event CG", nearby_text="Game Alpha CG update",
        signals={"socialdata_photo": True, "tweet_text": "Game Alpha event CG update"},
    )
    result = ImageCurator().curate(Issue(issue_id="261", input_path="261.docx", news_items=[item]), [candidate])
    assert candidate in result.candidates
    assert candidate.signals["source_linked"] is True
    assert candidate.signals["entity_match"] is True
    assert candidate.image_type.value == "game_cg"
    assert candidate.selected is True
