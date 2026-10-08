from datetime import datetime, timedelta, timezone
from io import BytesIO

from PIL import Image

from galgame_news.config import load_config
from galgame_news.domain import ImageCandidate, ImageCurationStatus, ImageNeed, NewsItem, ReviewReason, SourceType


def jpeg_bytes(size=(400, 400), color="red"):
    stream = BytesIO()
    Image.new("RGB", size, color).save(stream, format="JPEG")
    return stream.getvalue()


def candidate(url, *, news_id="n1", published_at=None, sha256=None, phash=None, signals=None, source_type=SourceType.OFFICIAL_SITE):
    return ImageCandidate(news_id=news_id, image_url=url, source_url="https://official.example/news", source_type=source_type, fetched_at=datetime.now(timezone.utc), published_at=published_at, width=1280, height=720, downloadable=True, sha256=sha256, perceptual_hash=phash, signals=signals or {})


def news(news_id="n1", importance=0.8):
    sequence = int(news_id.removeprefix("n")) if news_id.startswith("n") and news_id[1:].isdigit() else 1
    return NewsItem(id=news_id, issue_id="259", sequence=sequence, section="新作", title=f"《Game {news_id}》更新", body="", game_names=["Game"], source_urls=["https://official.example/news"], importance=importance, image_need=ImageNeed.EXPLICIT_NEW_IMAGE)


def test_validator_rejects_corrupt_and_fake_mime_and_accepts_boundary():
    from galgame_news.curation.validation import ImageValidator

    validator = ImageValidator(min_width=300, min_height=300, min_pixels=120000)
    assert validator.validate(jpeg_bytes((300, 400)), "image/jpeg").valid
    assert not validator.validate(b"not-a-jpeg", "image/jpeg").valid
    assert validator.validate(jpeg_bytes((299, 500)), "image/jpeg").valid
    mislabeled = validator.validate(jpeg_bytes((400, 400)), "text/html")
    assert mislabeled.valid
    assert mislabeled.mime_type == "image/jpeg"


def test_filter_semantics_and_hashes_mark_duplicates_and_fallback_old():
    from galgame_news.curation.dedup import Deduplicator

    first = candidate("https://cdn/a.jpg", sha256="a" * 64, phash="0011")
    exact = candidate("https://cdn/b.jpg", sha256="a" * 64, phash="9999")
    perceptual = candidate("https://cdn/c.jpg", sha256="c" * 64, phash="0011")
    old = candidate("https://cdn/old.jpg", published_at=datetime.now(timezone.utc) - timedelta(days=900), signals={"fallback_old_material": True})
    result = Deduplicator().deduplicate([first, exact, perceptual, old], historical_hashes={"oldsha": "old"})
    assert len(result.unique) == 3
    assert any(c.signals.get("duplicate_of") for c in result.duplicates)
    assert any(c.signals.get("possible_duplicate_of") for c in result.unique)
    assert ReviewReason.FALLBACK_OLD_MATERIAL in old.review_reasons


def test_dedup_uses_decoded_pixels_and_original_detail_not_bytes_or_upscaled_dimensions(tmp_path):
    from galgame_news.curation.dedup import Deduplicator

    pixels = Image.new("RGB", (640, 360), "#aa3344")
    png = tmp_path / "scene.png"
    webp = tmp_path / "scene.webp"
    pixels.save(png, format="PNG")
    pixels.save(webp, format="WEBP", quality=95)
    high = candidate("https://cdn/scene.png", signals={"visible_detail_score": 0.8})
    high.original_path = str(png)
    high.original_sha256 = "a" * 64
    high.perceptual_hash = "0011"
    high.original_width, high.original_height = 640, 360
    high.original_byte_size = 500
    low = candidate("https://cdn/scene.webp", signals={"visible_detail_score": 0.4})
    low.original_path = str(webp)
    low.original_sha256 = "b" * 64
    low.perceptual_hash = "0011"
    low.original_width, low.original_height = 320, 180
    low.original_byte_size = 8000
    low.width, low.height = 4000, 3000  # derived/upscaled dimensions do not win

    result = Deduplicator().deduplicate([low, high])
    assert result.unique == [high]
    assert result.duplicates == [low]
    assert low.selected is False
    assert low.curation_status is ImageCurationStatus.UNSELECTED
    assert low.signals["duplicate_of"] == high.id
    assert low.signals["duplicate_reason"] == "same_news_same_scene"
    assert low.image_url.endswith("scene.webp")


def test_dedup_does_not_merge_phash_collision_or_cross_news(tmp_path):
    from galgame_news.curation.dedup import Deduplicator

    def saved(path, color):
        Image.new("RGB", (320, 180), color).save(path, format="PNG")
    a_path, b_path = tmp_path / "a.png", tmp_path / "b.png"
    saved(a_path, "red")
    saved(b_path, "blue")
    a = candidate("https://cdn/a.png", sha256="a" * 64, phash="0000")
    b = candidate("https://cdn/b.png", sha256="b" * 64, phash="0003")
    a.original_path, b.original_path = str(a_path), str(b_path)
    cross_news = candidate("https://cdn/copy.png", news_id="n2", sha256="a" * 64)
    result = Deduplicator().deduplicate([a, b, cross_news])
    assert len(result.unique) == 3
    assert result.duplicates == []


def test_dedup_confirms_near_phash_versions_with_decoded_pixels(tmp_path):
    from galgame_news.curation.dedup import Deduplicator

    scene = Image.new("RGB", (640, 360), "#385a7c")
    full, variant = tmp_path / "full.png", tmp_path / "variant.webp"
    scene.save(full)
    scene.save(variant, format="WEBP", quality=95)
    a = candidate("https://cdn/full.png", sha256="a" * 64, phash="0000", signals={"visible_detail_score": 0.9})
    b = candidate("https://cdn/variant.webp", sha256="b" * 64, phash="001f", signals={"visible_detail_score": 0.5})
    a.original_path, b.original_path = str(full), str(variant)
    result = Deduplicator().deduplicate([b, a])
    assert result.unique == [a]
    assert result.duplicates == [b]
    assert b.signals["duplicate_kind"] == "perceptual_pixels"


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


def test_news_linked_official_game_gallery_is_selected_for_explicit_cg_update():
    from galgame_news.curation.curator import ImageCurator
    from galgame_news.domain import Issue

    root = "https://hook-net.jp/smee/one/"
    item = NewsItem(
        issue_id="262",
        sequence=7,
        section="新作",
        title="《One Night After》CG更新",
        body="官网更新了一组 CG 图片。",
        game_names=["One Night After"],
        source_urls=[root],
        image_need=ImageNeed.EXPLICIT_NEW_IMAGE,
    )
    gallery_image = ImageCandidate(
        news_id=item.id,
        image_url="http://www.hook-net.jp/smee/one/_assets/images/gallery/full/gallery_n_009.png",
        source_url="http://www.hook-net.jp/smee/one/gallery/",
        source_type=SourceType.OFFICIAL_SITE,
        fetched_at=datetime.now(timezone.utc),
        width=1280,
        height=720,
        downloadable=True,
        signals={
            "root_source_url": root,
            "gallery_path": True,
            "page_title": "GALLERY | SMEE 15th Project | ワンナイトアフター - SMEE",
        },
    )

    result = ImageCurator(load_config()).curate(
        Issue(issue_id="262", input_path="262.docx", news_items=[item]),
        [gallery_image],
    )

    assert gallery_image.selected is True
    assert gallery_image.image_type.value == "game_cg"
    assert gallery_image.image_type_confidence >= 0.8
    assert any(candidate.id == gallery_image.id and candidate.selected for candidate in result.candidates)


def test_ranker_prefers_720p_candidate_over_wide_but_short_banner():
    from galgame_news.curation.ranker import ImageRanker

    item = news()
    banner = candidate("https://official.example/gallery/banner.jpg", signals={"cg_match": 1.0, "source_officiality": 1.0})
    banner.width, banner.height = 1920, 540
    cg = candidate("https://official.example/gallery/cg01.jpg", signals={"cg_match": 1.0, "source_officiality": 1.0})
    cg.width, cg.height = 1280, 720
    ranked = ImageRanker(load_config().scoring).rank(item, [banner, cg])
    assert ranked[0] is cg


def test_default_selection_order_does_not_change_when_only_publication_dates_change():
    from galgame_news.curation.curator import ImageCurator
    from galgame_news.domain import Issue

    item = news("n1")
    values = [
        candidate("https://official.example/gallery/cg01.jpg", news_id=item.id, signals={"game_match": 1.0}),
        candidate("https://official.example/gallery/cg02.jpg", news_id=item.id, signals={"game_match": 1.0}),
    ]
    issue = Issue(issue_id="259", input_path="259.docx", news_items=[item])
    curator = ImageCurator(load_config())
    first = curator.curate(issue, [value.model_copy(deep=True) for value in values])
    changed_dates = [value.model_copy(deep=True) for value in values]
    changed_dates[0].published_at = datetime(1999, 1, 1, tzinfo=timezone.utc)
    changed_dates[1].published_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    second = curator.curate(issue, changed_dates)
    assert [value.id for value in first.candidates if value.selected] == [value.id for value in second.candidates if value.selected]
    assert [value.score.total for value in first.candidates] == [value.score.total for value in second.candidates]


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
    values = [candidate(f"https://official.example/gallery/cg{index}.jpg", news_id=item.id, signals={"game_match": 1.0}) for index in range(8)]
    result = ImageCurator().curate(Issue(issue_id="259", input_path="259.docx", news_items=[item]), values)
    assert len(result.candidates) == 8
    assert not any(value.selected for value in result.candidates)
    assert all("image_type_review" in {reason.value for reason in value.review_reasons} for value in result.candidates)


def test_validator_rejects_avif_header_without_decodable_pixels():
    from galgame_news.curation.validation import ImageValidator

    avif = b"\x00\x00\x00\x18ftypavif\x00\x00\x00\x00avifmif1" + b"payload"
    result = ImageValidator().validate(avif, "image/avif")
    assert not result.valid
    assert result.reason == "corrupt_image"


def test_semantic_filter_rejects_logo_banner_and_thumbnail_urls():
    from galgame_news.curation.validation import meaningless_asset_reason

    # Semantic image type is a selection decision; these files can still be
    # useful to an editor and must make it through downloading.
    assert meaningless_asset_reason(candidate("https://site.example/assets/logo.png")) is None
    assert meaningless_asset_reason(candidate("https://site.example/header/banner.jpg")) is None
    assert meaningless_asset_reason(candidate("https://site.example/cg/event01.jpg")) is None
    assert meaningless_asset_reason(candidate("https://static.parastorage.com/services/santa-resources/resources/viewer/editorUI/fonts.v19.png")) == "invalid_material"


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
    assert accepted[0].original_path == accepted[0].local_path
    assert accepted[0].original_mime_type == "image/jpeg"
    assert accepted[0].original_sha256 == accepted[0].sha256
    assert 0.0 <= accepted[0].signals["sharpness_score"] <= 1.0
    assert accepted[0].original_width == 800
    assert calls == [duplicate_url]


def test_download_quality_signals_measure_blur_and_blockiness_without_byte_size():
    from io import BytesIO
    from PIL import Image, ImageDraw, ImageFilter
    from galgame_news.curation.download import _image_quality_signals

    image = Image.new("RGB", (256, 256), "white")
    draw = ImageDraw.Draw(image)
    for x in range(0, 256, 16):
        draw.rectangle((x, 0, x + 7, 255), fill="black")
    sharp = BytesIO()
    image.save(sharp, format="JPEG", quality=35)
    blurred = BytesIO()
    image.filter(ImageFilter.GaussianBlur(5)).save(blurred, format="JPEG", quality=90)

    sharp_signals = _image_quality_signals(sharp.getvalue(), 256, 256)
    blurred_signals = _image_quality_signals(blurred.getvalue(), 256, 256)

    assert sharp_signals["sharpness_score"] > blurred_signals["sharpness_score"]
    assert "blockiness_score" in sharp_signals
    assert "visible_detail_score" in sharp_signals


def test_downloader_preserves_semantic_assets_for_review(tmp_path):
    from galgame_news.curation.download import ImageDownloader
    calls = []
    def transport(url, **_):
        calls.append(url)
        raise AssertionError("invalid asset should not be requested")
    values = [candidate(f"https://site.example/assets/{token}.jpg") for token in ["logo", "banner", "thumbnail"]]
    values.append(candidate("https://site.example/assets/vector.svg"))
    payload = jpeg_bytes((180, 120))
    def transport(url, **_):
        calls.append(url)
        if url.endswith(".svg"):
            return type("Response", (), {"content": b"<svg/>", "headers": {"content-type": "image/svg+xml"}, "status_code": 200, "url": url})()
        return type("Response", (), {"content": payload, "headers": {"content-type": "image/jpeg"}, "status_code": 200, "url": url})()
    accepted, failures = ImageDownloader(load_config(), transport=transport).download(values, tmp_path)
    assert len(accepted) == 4
    assert sum(value.downloadable and value.width == 180 for value in accepted) == 3
    assert any(value.signals.get("invalid_reason") == "invalid_material" for value in accepted)
    assert len(failures) == 1


def test_corrupt_download_stays_in_curated_invalid_records(tmp_path):
    from galgame_news.curation.curator import ImageCurator
    from galgame_news.curation.download import ImageDownloader
    from galgame_news.domain import Issue

    item = news("n1")
    value = candidate("https://official.example/gallery/cg01.jpg", news_id=item.id, signals={"page_title": "Game CG"})
    def transport(url, **_):
        return type("Response", (), {"content": b"not-an-image", "headers": {"content-type": "image/jpeg"}, "status_code": 200, "url": url})()
    downloaded, failures = ImageDownloader(load_config(), transport=transport).download([value], tmp_path)
    curated = ImageCurator().curate(Issue(issue_id="259", input_path="259.docx", news_items=[item]), downloaded)
    all_records = [*curated.candidates, *curated.filtered_candidates]
    assert [record.id for record in all_records] == [value.id]
    assert all_records[0].curation_status is ImageCurationStatus.INVALID
    assert all_records[0].signals["invalid_reason"] == "corrupt_image"
    assert all_records[0].selected is False
    assert failures[0].code == "corrupt_image"


def test_curator_returns_same_news_duplicates_and_keeps_news_groups_independent():
    from galgame_news.curation.curator import ImageCurator
    from galgame_news.domain import Issue

    first_news = news("n1")
    second_news = news("n2")
    proof = {"page_title": "Game official CG", "game_match": 1.0}
    duplicate_a = candidate("https://official.example/gallery/cg01.jpg", news_id=first_news.id, sha256="a" * 64, signals=proof)
    duplicate_b = candidate("https://cdn.example/copy.jpg", news_id=first_news.id, sha256="a" * 64, signals=proof)
    cross_news_copy = candidate("https://official.example/gallery/cg02.jpg", news_id=second_news.id, sha256="a" * 64, signals=proof)
    result = ImageCurator().curate(
        Issue(issue_id="259", input_path="259.docx", news_items=[first_news, second_news]),
        [duplicate_a, duplicate_b, cross_news_copy],
    )
    assert {value.id for value in result.candidates} == {duplicate_a.id, duplicate_b.id, cross_news_copy.id}
    assert duplicate_a.selected is True
    assert duplicate_b.selected is False
    assert cross_news_copy.selected is True
    assert duplicate_b.signals.get("duplicate_of") == duplicate_a.id


def test_curator_requires_candidate_source_chain_to_reach_this_news():
    from galgame_news.curation.curator import ImageCurator
    from galgame_news.domain import Issue

    item = news("n1")
    item.source_urls = ["https://official.example/news/item"]
    linked = candidate(
        "https://cdn.example/gallery/cg01.jpg", news_id=item.id,
        signals={"root_source_url": "https://official.example/news/item", "page_title": "Game CG"},
    )
    same_domain_other_page = candidate(
        "https://cdn.example/gallery/cg02.jpg", news_id=item.id,
        signals={"root_source_url": "https://official.example/news/other", "page_title": "Game CG"},
    )
    result = ImageCurator().curate(Issue(issue_id="259", input_path="259.docx", news_items=[item]), [linked, same_domain_other_page])
    assert linked.selected is True
    assert same_domain_other_page.selected is False
    assert same_domain_other_page.signals["source_linked"] is False
    assert any(f.code == "news_source_unlinked" for f in result.failures)


def test_small_decodable_image_is_retained_but_cannot_auto_select():
    from galgame_news.curation.curator import ImageCurator
    from galgame_news.domain import Issue

    item = news("n1")
    value = candidate("https://official.example/gallery/cg01.jpg", news_id=item.id, signals={"page_title": "Game CG"})
    value.width, value.height = 180, 120
    result = ImageCurator().curate(Issue(issue_id="259", input_path="259.docx", news_items=[item]), [value])
    assert value in result.candidates
    assert value.selected is False
    assert value.signals["quality_eligible"] is False
    assert ReviewReason.IMAGE_QUALITY_REVIEW in value.review_reasons


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
