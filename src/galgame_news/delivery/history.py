"""SQLite and in-memory implementations of the history seam."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from ..domain import (
    HistoricalImage,
    ImageCandidate,
    NewsItem,
    NewsResult,
    SourceRef,
)


SCHEMA_VERSION = 1


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _enum_values(values) -> list[str]:
    return [value.value for value in values]


class SQLiteHistoryStore:
    """Durable history store with one transaction per news result."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._initialize()

    def _initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS news_items (
                news_id TEXT PRIMARY KEY,
                issue_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                title TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sources (
                source_id INTEGER PRIMARY KEY AUTOINCREMENT,
                news_id TEXT NOT NULL REFERENCES news_items(news_id) ON DELETE CASCADE,
                url TEXT NOT NULL,
                source_type TEXT NOT NULL,
                domain TEXT NOT NULL,
                discovered_via TEXT NOT NULL,
                officiality REAL NOT NULL,
                requires_review INTEGER NOT NULL,
                review_reasons_json TEXT NOT NULL,
                UNIQUE(news_id, url)
            );
            CREATE TABLE IF NOT EXISTS images (
                image_id TEXT PRIMARY KEY,
                sha256 TEXT,
                perceptual_hash TEXT,
                first_seen_issue TEXT NOT NULL,
                last_seen_issue TEXT NOT NULL,
                local_path TEXT
            );
            CREATE TABLE IF NOT EXISTS image_sightings (
                sighting_id INTEGER PRIMARY KEY AUTOINCREMENT,
                image_id TEXT NOT NULL REFERENCES images(image_id) ON DELETE CASCADE,
                issue_id TEXT NOT NULL,
                news_id TEXT NOT NULL,
                image_url TEXT NOT NULL,
                source_url TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                UNIQUE(image_id, issue_id, news_id)
            );
            CREATE TABLE IF NOT EXISTS news_images (
                news_id TEXT NOT NULL REFERENCES news_items(news_id) ON DELETE CASCADE,
                image_id TEXT NOT NULL REFERENCES images(image_id) ON DELETE CASCADE,
                selected INTEGER NOT NULL,
                score_json TEXT,
                PRIMARY KEY(news_id, image_id)
            );
            CREATE TABLE IF NOT EXISTS official_domains (
                domain TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                officiality REAL NOT NULL,
                last_seen_issue TEXT NOT NULL
            );
            PRAGMA user_version = 1;
            """
        )
        self.connection.commit()

    def known_image(self, sha256: str | None, perceptual_hash: str | None) -> HistoricalImage | None:
        clauses = []
        params: list[str] = []
        if sha256:
            clauses.append("sha256 = ?")
            params.append(sha256)
        if perceptual_hash:
            clauses.append("perceptual_hash = ?")
            params.append(perceptual_hash)
        if not clauses:
            return None
        row = self.connection.execute(
            f"SELECT image_id, sha256, perceptual_hash, first_seen_issue, last_seen_issue, local_path FROM images WHERE {' OR '.join(clauses)} ORDER BY image_id LIMIT 1",
            params,
        ).fetchone()
        return HistoricalImage(**dict(row)) if row else None

    def sources_for(self, news_item: NewsItem) -> list[SourceRef]:
        rows = self.connection.execute(
            "SELECT url, source_type, domain, discovered_via, officiality, requires_review, review_reasons_json FROM sources WHERE news_id = ? ORDER BY source_id",
            (news_item.id,),
        ).fetchall()
        return [
            SourceRef(
                url=row["url"],
                source_type=row["source_type"],
                domain=row["domain"],
                discovered_via=row["discovered_via"],
                officiality=row["officiality"],
                requires_review=bool(row["requires_review"]),
                review_reasons=json.loads(row["review_reasons_json"]),
            )
            for row in rows
        ]

    def record_news_result(self, result: NewsResult) -> None:
        news = result.news_item
        with self.connection:
            self.connection.execute(
                "INSERT INTO runs(issue_id, created_at) VALUES (?, ?)",
                (news.issue_id, _iso(result.recorded_at)),
            )
            self.connection.execute(
                """INSERT INTO news_items(news_id, issue_id, sequence, title, payload_json)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(news_id) DO UPDATE SET payload_json=excluded.payload_json, title=excluded.title""",
                (news.id, news.issue_id, news.sequence, news.title, news.model_dump_json()),
            )
            for source in result.sources:
                self.connection.execute(
                    """INSERT INTO sources(news_id, url, source_type, domain, discovered_via, officiality, requires_review, review_reasons_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(news_id, url) DO UPDATE SET officiality=excluded.officiality, requires_review=excluded.requires_review, review_reasons_json=excluded.review_reasons_json""",
                    (
                        news.id,
                        source.url,
                        source.source_type.value,
                        source.domain,
                        source.discovered_via.value,
                        source.officiality,
                        int(source.requires_review),
                        json.dumps(_enum_values(source.review_reasons), ensure_ascii=False),
                    ),
                )
                if source.officiality >= 0.8:
                    self.connection.execute(
                        """INSERT INTO official_domains(domain, source_type, officiality, last_seen_issue)
                           VALUES (?, ?, ?, ?)
                           ON CONFLICT(domain) DO UPDATE SET officiality=MAX(officiality, excluded.officiality), last_seen_issue=excluded.last_seen_issue""",
                        (source.domain, source.source_type.value, source.officiality, news.issue_id),
                    )
            for candidate in result.candidates:
                self._record_candidate(news, candidate)

    def _record_candidate(self, news: NewsItem, candidate: ImageCandidate) -> None:
        existing = self.connection.execute(
            "SELECT first_seen_issue FROM images WHERE image_id = ?", (candidate.id,)
        ).fetchone()
        first_issue = existing[0] if existing else news.issue_id
        self.connection.execute(
            """INSERT INTO images(image_id, sha256, perceptual_hash, first_seen_issue, last_seen_issue, local_path)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(image_id) DO UPDATE SET sha256=excluded.sha256, perceptual_hash=excluded.perceptual_hash, last_seen_issue=excluded.last_seen_issue, local_path=COALESCE(excluded.local_path, images.local_path)""",
            (candidate.id, candidate.sha256, candidate.perceptual_hash, first_issue, news.issue_id, candidate.local_path),
        )
        self.connection.execute(
            """INSERT INTO image_sightings(image_id, issue_id, news_id, image_url, source_url, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(image_id, issue_id, news_id) DO UPDATE SET fetched_at=excluded.fetched_at""",
            (candidate.id, news.issue_id, news.id, candidate.image_url, candidate.source_url, _iso(candidate.fetched_at)),
        )
        self.connection.execute(
            """INSERT INTO news_images(news_id, image_id, selected, score_json)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(news_id, image_id) DO UPDATE SET selected=excluded.selected, score_json=excluded.score_json""",
            (news.id, candidate.id, int(candidate.selected), candidate.score.model_dump_json() if candidate.score else None),
        )

    def close(self) -> None:
        self.connection.close()


class MemoryHistoryStore:
    """Small deterministic store for offline tests and future application wiring."""

    def __init__(self):
        self._images: dict[str, HistoricalImage] = {}
        self._sources: dict[str, list[SourceRef]] = {}

    def known_image(self, sha256: str | None, perceptual_hash: str | None) -> HistoricalImage | None:
        for image in self._images.values():
            if (sha256 and image.sha256 == sha256) or (perceptual_hash and image.perceptual_hash == perceptual_hash):
                return image
        return None

    def sources_for(self, news_item: NewsItem) -> list[SourceRef]:
        return list(self._sources.get(news_item.id, []))

    def record_news_result(self, result: NewsResult) -> None:
        self._sources[result.news_item.id] = list(result.sources)
        for candidate in result.candidates:
            existing = self._images.get(candidate.id)
            self._images[candidate.id] = HistoricalImage(
                image_id=candidate.id,
                sha256=candidate.sha256,
                perceptual_hash=candidate.perceptual_hash,
                first_seen_issue=existing.first_seen_issue if existing else result.news_item.issue_id,
                last_seen_issue=result.news_item.issue_id,
                local_path=candidate.local_path or (existing.local_path if existing else None),
            )
