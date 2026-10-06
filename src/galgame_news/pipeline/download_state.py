"""Atomic news-local download records, independent of transient staging."""

from pathlib import Path
import hashlib
import json

from ..curation.download import ImageDownloader
from ..delivery.helpers import atomic_json_write
from ..domain import ImageCandidate


PUBLIC_MEDIA_KEYS = frozenset({"tweet", "data", "tweets", "id", "id_str", "full_text", "text",
    "entities", "extended_entities", "extendedEntities", "attachments", "includes", "media",
    "photos", "images", "image_urls", "media_keys", "media_key", "key", "type", "media_type",
    "media_url_https", "media_url", "image_url", "url", "alt_text", "alt", "original_info",
    "width", "height", "video_info", "videoInfo", "variants", "bitrate", "content_type", "contentType"})


def public_media_payload(value):
    if isinstance(value, dict):
        return {key: public_media_payload(item) for key,item in value.items() if key in PUBLIC_MEDIA_KEYS}
    if isinstance(value, list):
        return [public_media_payload(item) for item in value]
    return value if isinstance(value, (str, int, float, bool, type(None))) else None


class DownloadJournal:
    def __init__(self, task_root, issue, input_hash, config_hash):
        self.root = Path(task_root).resolve()
        self.directory = self.root / "media_state"
        self.news_ids = {news.id for news in issue.news_items}
        self.identity = {"input_sha256": input_hash, "config_sha256": config_hash}

    def save(self, news_id, candidates):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / (hashlib.sha256(news_id.encode()).hexdigest()[:20] + ".json")
        atomic_json_write(path, {**self.identity, "news_id": news_id,
            "candidates": [value.model_dump(mode="json") for value in candidates if value.news_id == news_id]})

    def load(self):
        values = []
        collected = set()
        for path in self.directory.glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            if any(payload.get(key) != value for key, value in self.identity.items()):
                raise ValueError("download journal identity mismatch")
            news_id = payload["news_id"]
            if news_id not in self.news_ids:
                raise ValueError("download journal news id is unknown")
            for raw in payload["candidates"]:
                candidate = ImageCandidate.model_validate(raw)
                if candidate.news_id != news_id:
                    raise ValueError("download journal candidate news mismatch")
                self.confine(candidate)
                values.append(candidate)
            collected.add(news_id)
        return values, collected

    def confine(self, candidate):
        for value in (candidate.local_path, candidate.original_path):
            if value and not Path(value).resolve().is_relative_to(self.root):
                raise ValueError("download asset escapes task directory")

    def needs_download(self, candidate, failures=()):
        self.confine(candidate)
        if ImageDownloader.reusable(candidate):
            return False
        if candidate.download_status == "permanent_failed":
            return False
        related = [failure for failure in failures if failure.candidate_id == candidate.id
                   and failure.stage.value == "download"]
        if candidate.download_status == "pending" and candidate.signals.get("invalid_reason"):
            if not any(failure.retryable for failure in related):
                return False
        return True

    def merge(self, candidates, saved):
        by_id = {candidate.id: candidate for candidate in candidates}
        for candidate in saved:
            by_id[candidate.id] = candidate
        return list(by_id.values())

    def save_post(self, post_id, state, payload=None):
        if not post_id.isdecimal():
            raise ValueError("invalid post id")
        directory = self.root / "x_media_cache"
        directory.mkdir(parents=True, exist_ok=True)
        atomic_json_write(directory/(post_id+".json"), {**self.identity, "state":state,
            "payload":public_media_payload(payload) if payload is not None else None})

    def load_posts(self):
        result = {}
        for path in (self.root/"x_media_cache").glob("*.json"):
            if not path.stem.isdecimal():
                raise ValueError("invalid cached post id")
            payload = json.loads(path.read_text(encoding="utf-8"))
            if any(payload.get(key)!=value for key,value in self.identity.items()):
                raise ValueError("post cache identity mismatch")
            result[path.stem] = payload["payload"] if payload["state"]=="success" else None
        return result
