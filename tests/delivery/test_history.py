from datetime import datetime, timezone
import sqlite3

import pytest

from galgame_news.domain import (
    EventType,
    FailureRecord,
    FailureStage,
    ImageCandidate,
    ImageNeed,
    NewsItem,
    NewsResult,
    ReviewReason,
    SourceRef,
    SourceType,
    DiscoveryMethod,
)
from galgame_news.delivery.history import SQLiteHistoryStore


def make_news() -> NewsItem:
    return NewsItem(
        issue_id="259",
        sequence=1,
        section="新作",
        title="Happy Weekend",
        body="主视觉图公开",
        game_names=["Happy Weekend"],
        event_type=EventType.UPDATE,
        image_need=ImageNeed.EXPLICIT_NEW_IMAGE,
    )


def make_source() -> SourceRef:
    return SourceRef(
        url="https://official.example/news/1",
        source_type=SourceType.OFFICIAL_SITE,
        domain="official.example",
        discovered_via=DiscoveryMethod.DOCUMENT,
        officiality=0.95,
    )


def make_candidate() -> ImageCandidate:
    return ImageCandidate(
        news_id=make_news().id,
        image_url="https://cdn.official.example/cg/01.jpg",
        source_url="https://official.example/news/1",
        source_type=SourceType.OFFICIAL_SITE,
        published_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
        width=1200,
        height=675,
        mime_type="image/jpeg",
        byte_size=12345,
        sha256="a" * 64,
        perceptual_hash="0123456789abcdef",
        downloadable=True,
        local_path="images/news-1/01.jpg",
    )


def make_result() -> NewsResult:
    return NewsResult(news_item=make_news(), sources=[make_source()], candidates=[make_candidate()])


def test_sqlite_store_initializes_schema_and_is_idempotent(tmp_path):
    path = tmp_path / "history.sqlite3"
    first = SQLiteHistoryStore(path)
    second = SQLiteHistoryStore(path)

    tables = {
        row[0]
        for row in second.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"runs", "news_items", "sources", "images", "image_sightings", "news_images", "official_domains"} <= tables
    assert second.connection.execute("PRAGMA user_version").fetchone()[0] == 1
    first.close()
    second.close()


def test_sqlite_store_records_sources_images_and_supports_history_queries(tmp_path):
    store = SQLiteHistoryStore(tmp_path / "history.sqlite3")
    result = make_result()
    store.record_news_result(result)

    known = store.known_image(result.candidates[0].sha256, None)
    assert known is not None
    assert known.first_seen_issue == "259"
    assert store.sources_for(result.news_item)[0].domain == "official.example"
    store.close()


def test_sqlite_store_matches_perceptual_hash_when_exact_hash_is_unknown(tmp_path):
    store = SQLiteHistoryStore(tmp_path / "history.sqlite3")
    result = make_result()
    store.record_news_result(result)

    known = store.known_image(None, result.candidates[0].perceptual_hash)
    assert known is not None
    assert known.sha256 == "a" * 64
    store.close()


def test_sqlite_record_is_atomic_when_a_later_insert_fails(tmp_path):
    store = SQLiteHistoryStore(tmp_path / "history.sqlite3")
    store.connection.execute(
        """CREATE TRIGGER fail_image_sighting BEFORE INSERT ON image_sightings
           BEGIN SELECT RAISE(ABORT, 'fixture failure'); END;"""
    )

    with pytest.raises(sqlite3.IntegrityError, match="fixture failure"):
        store.record_news_result(make_result())

    assert store.connection.execute("SELECT COUNT(*) FROM news_items").fetchone()[0] == 0
    assert store.connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
    assert store.connection.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 0
    store.close()
