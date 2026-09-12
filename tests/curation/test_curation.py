from datetime import datetime, timedelta, timezone
from io import BytesIO

from PIL import Image

from galgame_news.config import load_config
from galgame_news.domain import ImageCandidate, ImageNeed, NewsItem, ReviewReason, SourceType


def jpeg_bytes(size=(400, 400), color="red"):
    stream = BytesIO()
    Image.new("RGB", size, color).save(stream, format="JPEG")
    return stream.getvalue()


def candidate(url, *, news_id="n1", published_at=None, sha256=None, phash=None, signals=None, source_type=SourceType.OFFICIAL_SITE):
    return ImageCandidate(news_id=news_id, image_url=url, source_url="https://official.example/news", source_type=source_type, fetched_at=datetime.now(timezone.utc), published_at=published_at, sha256=sha256, perceptual_hash=phash, signals=signals or {})


def news(news_id="n1", importance=0.8):
    sequence = int(news_id.removeprefix("n")) if news_id.startswith("n") and news_id[1:].isdigit() else 1
    return NewsItem(id=news_id, issue_id="259", sequence=sequence, section="新作", title=f"《Game {news_id}》更新", body="", game_names=["Game"], importance=importance, image_need=ImageNeed.EXPLICIT_NEW_IMAGE)


def test_validator_rejects_corrupt_and_fake_mime_and_accepts_boundary():
    from galgame_news.curation.validation import ImageValidator

    validator = ImageValidator(min_width=300, min_height=300, min_pixels=120000)
    assert validator.validate(jpeg_bytes((300, 400)), "image/jpeg").valid
    assert not validator.validate(b"not-a-jpeg", "image/jpeg").valid
    assert not validator.validate(jpeg_bytes((299, 500)), "image/jpeg").valid
    assert not validator.validate(jpeg_bytes((400, 400)), "text/html").valid


def test_filter_semantics_and_hashes_mark_duplicates_and_fallback_old():
    from galgame_news.curation.dedup import Deduplicator

    first = candidate("https://cdn/a.jpg", sha256="a" * 64, phash="0011")
    exact = candidate("https://cdn/b.jpg", sha256="a" * 64, phash="9999")
    perceptual = candidate("https://cdn/c.jpg", sha256="c" * 64, phash="0011")
    old = candidate("https://cdn/old.jpg", published_at=datetime.now(timezone.utc) - timedelta(days=900), signals={"fallback_old_material": True})
    result = Deduplicator().deduplicate([first, exact, perceptual, old], historical_hashes={"oldsha": "old"})
    assert len(result.unique) == 2
    assert any(ReviewReason.HISTORICAL_DUPLICATE in c.review_reasons for c in result.duplicates)
    assert ReviewReason.FALLBACK_OLD_MATERIAL in old.review_reasons


def test_ranker_uses_config_weights_unknown_date_review_and_deterministic_ties(tmp_path):
    from galgame_news.curation.ranker import ImageRanker

    config = load_config()
    ranker = ImageRanker(config.scoring)
    ranked = ranker.rank(news(), [candidate("https://cdn/z.jpg", signals={"game_match": 1.0, "page_match": 1.0}), candidate("https://cdn/a.jpg")])
    assert ranked[0].score.total >= ranked[1].score.total
    assert ReviewReason.UNKNOWN_PUBLISH_TIME in ranked[1].review_reasons


def test_allocator_limits_per_news_and_total_and_reports_shortfall():
    from galgame_news.curation.allocator import ImageAllocator

    items = [news("n1", 0.9), news("n2", 0.8), news("n3", 0.7)]
    ids = [item.id for item in items]
    candidates = [candidate(f"https://cdn/{i}.jpg", news_id=ids[0] if i < 5 else ids[1] if i < 7 else ids[2], signals={"game_match": 1.0}) for i in range(8)]
    result = ImageAllocator(min_images=5, max_images=6, per_news_max=3).allocate(items, candidates)
    assert len([c for c in result if c.selected]) == 6
    assert all(sum(c.selected for c in result if c.news_id == item.id) <= 3 for item in items)
    short = ImageAllocator(min_images=5, max_images=20).allocate(items[:1], candidates[:2])
    assert sum(c.selected for c in short) == 2
    assert getattr(short, "selection_shortfall", 3) >= 3
