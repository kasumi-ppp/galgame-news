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


def test_ranker_honors_resolver_officiality_instead_of_trusting_every_document_site():
    from galgame_news.curation.ranker import ImageRanker

    value = candidate("https://third-party.example/image.jpg", signals={"source_officiality": 0.25})
    ranked = ImageRanker(load_config().scoring).rank(news(), [value])
    assert ranked[0].score.source_trust == 0.25


def test_ranker_prefers_gallery_cg_over_larger_generic_asset_for_cg_news():
    from galgame_news.curation.ranker import ImageRanker

    item = news()
    gallery = candidate(
        "https://official.example/gallery/cg01_a.jpg",
        signals={"cg_match": 1.0, "source_officiality": 1.0},
    )
    gallery.width, gallery.height = 1000, 563
    generic = candidate(
        "https://official.example/story/background.png",
        signals={"source_officiality": 1.0},
    )
    generic.width, generic.height = 2000, 1200
    ranked = ImageRanker(load_config().scoring).rank(item, [generic, gallery])
    assert ranked[0] is gallery


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


def test_allocator_targets_five_to_twenty_per_news_without_issue_cap():
    from galgame_news.curation.allocator import ImageAllocator

    items = [news("n1", 0.9), news("n2", 0.8)]
    ids = [item.id for item in items]
    candidates = [candidate(f"https://cdn/all-{i}.jpg", news_id=ids[0] if i < 8 else ids[1], signals={"game_match": 1.0}) for i in range(16)]
    result = ImageAllocator(min_images=5, max_images=None, per_news_max=20).allocate(items, candidates)
    assert sum(c.selected for c in result if c.news_id == ids[0]) == 8
    assert sum(c.selected for c in result if c.news_id == ids[1]) == 8
    assert result.selection_shortfall == 0


def test_curator_default_does_not_truncate_a_news_item_to_three_candidates():
    from galgame_news.curation.curator import ImageCurator
    from galgame_news.domain import Issue

    item = news("n1")
    values = [candidate(f"https://cdn/{index}.jpg", news_id=item.id, signals={"game_match": 1.0}) for index in range(8)]
    result = ImageCurator().curate(Issue(issue_id="259", input_path="259.docx", news_items=[item]), values)
    assert sum(value.selected for value in result.candidates) == 8


def test_validator_keeps_avif_magic_when_decoder_is_unavailable():
    from galgame_news.curation.validation import ImageValidator

    avif = b"\x00\x00\x00\x18ftypavif\x00\x00\x00\x00avifmif1" + b"payload"
    result = ImageValidator().validate(avif, "image/avif")
    assert result.valid
    assert result.mime_type == "image/avif"


def test_semantic_filter_rejects_logo_banner_and_thumbnail_urls():
    from galgame_news.curation.validation import meaningless_asset_reason

    assert meaningless_asset_reason(candidate("https://site.example/assets/logo.png")) == "meaningless_asset"
    assert meaningless_asset_reason(candidate("https://site.example/header/banner.jpg")) == "meaningless_asset"
    assert meaningless_asset_reason(candidate("https://site.example/cg/event01.jpg")) is None


def test_downloader_fetches_same_news_image_url_only_once(tmp_path):
    from galgame_news.curation.download import ImageDownloader

    calls = []
    payload = jpeg_bytes((800, 600))

    def transport(url, **_):
        calls.append(url)
        return type("Response", (), {"content": payload, "headers": {"content-type": "image/jpeg"}, "status_code": 200, "url": url})()

    duplicate_url = "https://official.example/gallery/cg01.jpg"
    accepted, failures = ImageDownloader(load_config(), transport=transport).download(
        [candidate(duplicate_url), candidate(duplicate_url)],
        tmp_path,
    )
    assert not failures
    assert len(accepted) == 1
    assert calls == [duplicate_url]


def test_downloader_filters_invalid_material_before_request(tmp_path):
    from galgame_news.curation.download import ImageDownloader
    calls = []
    def transport(url, **_):
        calls.append(url)
        raise AssertionError("invalid asset should not be requested")
    values = [candidate(f"https://site.example/assets/{token}.jpg") for token in ["logo", "icon", "sprite", "button", "banner", "thumbnail", "profile_images/avatar"]]
    values.append(candidate("https://site.example/assets/vector.svg"))
    accepted, failures = ImageDownloader(load_config(), transport=transport).download(values, tmp_path)
    assert accepted == []
    assert calls == []
    assert len(failures) == len(values)
    assert all(f.code == "filtered_invalid_material" for f in failures)


def test_curator_uses_known_image_method_without_image_hashes_attribute():
    from galgame_news.curation.curator import ImageCurator
    from galgame_news.domain import HistoricalImage, Issue
    item = news("n1")
    value = candidate("https://cdn/repeated.jpg", news_id=item.id, sha256="a" * 64, phash="beef", signals={"game_match": 1.0})
    class History:
        def known_image(self, sha256, perceptual_hash):
            assert sha256 == "a" * 64
            return HistoricalImage(image_id="old", sha256=sha256, perceptual_hash=perceptual_hash, first_seen_issue="258", last_seen_issue="258")
    result = ImageCurator().curate(Issue(issue_id="259", input_path="x", news_items=[item]), [value], History())
    assert ReviewReason.HISTORICAL_DUPLICATE in result.candidates[0].review_reasons
