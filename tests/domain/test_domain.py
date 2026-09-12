from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from galgame_news.domain import (
    EventType,
    ImageCandidate,
    ImageNeed,
    Issue,
    IssueDraft,
    NewsDraft,
    NewsItem,
    ReviewReason,
    ScoreBreakdown,
    SourceRef,
    SourceType,
    DiscoveryMethod,
    news_id_for,
)


def test_news_id_is_stable_for_equivalent_titles():
    assert news_id_for("259", 1, "  Happy   Weekend ") == news_id_for("259", 1, "Happy Weekend")


def test_news_item_requires_timezone_aware_event_time():
    with pytest.raises(ValidationError, match="timezone"):
        NewsItem(
            issue_id="259",
            sequence=1,
            section="新作",
            title="Happy Weekend",
            body="主视觉图公开",
            event_type=EventType.UPDATE,
            event_at=datetime(2026, 9, 12),
            image_need=ImageNeed.EXPLICIT_NEW_IMAGE,
        )


def test_unknown_values_use_explicit_unknown_enum_members():
    item = NewsItem(
        issue_id="259",
        sequence=1,
        section="其他",
        title="未知事件",
        body="暂无更多信息",
        event_type="unknown",
        image_need="unknown",
    )
    assert item.event_type is EventType.UNKNOWN
    assert item.image_need is ImageNeed.UNKNOWN
    assert item.author is None


def test_image_candidate_derives_stable_id_and_rejects_empty_unknowns():
    candidate = ImageCandidate(
        news_id="news-1",
        image_url="https://official.example/cg/01.jpg",
        source_url="https://official.example/news/1",
        source_type=SourceType.OFFICIAL_SITE,
        fetched_at=datetime.now(timezone.utc),
        signals={"official_page": True},
    )
    assert len(candidate.id) == 16
    assert candidate.selected is False
    assert candidate.review_reasons == []

    with pytest.raises(ValidationError):
        SourceRef(
            url="https://official.example/news/1",
            source_type=SourceType.UNKNOWN,
            domain="",
            discovered_via=DiscoveryMethod.DOCUMENT,
            officiality=0.5,
        )


def test_issue_draft_and_issue_keep_deterministic_schema_version():
    draft = IssueDraft(
        issue_id="259",
        input_path="input/259.docx",
        entries=[NewsDraft(sequence=1, section="新作", title="作品", body="正文")],
    )
    issue = Issue(
        issue_id=draft.issue_id,
        input_path=draft.input_path,
        news_items=[
            NewsItem(
                issue_id="259",
                sequence=1,
                section="新作",
                title="作品",
                body="正文",
                event_type=EventType.UNKNOWN,
                image_need=ImageNeed.UNKNOWN,
            )
        ],
    )
    assert issue.schema_version == 1
    assert issue.model_dump(mode="json")["schema_version"] == 1


def test_schema_version_rejects_values_other_than_contract_version_one():
    with pytest.raises(ValidationError):
        Issue(schema_version=2, issue_id="259", input_path="input/259.docx", news_items=[])


def test_score_breakdown_clamps_each_component_to_unit_interval():
    with pytest.raises(ValidationError):
        ScoreBreakdown(
            relevance=1.1,
            freshness=0.0,
            source_trust=0.0,
            quality=0.0,
            duplicate_penalty=0.0,
            risk_penalty=0.0,
            total=0.0,
        )
