from datetime import datetime, timezone
import hashlib

from PIL import Image, ImageDraw

from galgame_news.curation.curator import ImageCurator
from galgame_news.curation.image_typing import ImageTypeClassifier
from galgame_news.domain import ImageCandidate, ImageType, Issue, NewsItem, SourceType


ROOT = "https://publisher.example/product/studio/one/"
PAGE = ROOT + "gallery/"


def news(title="《One Night After》官网更新"):
    return NewsItem(issue_id="262", sequence=1, section="新作", title=title, body="官网图库更新",
                    game_names=["One Night After"], source_urls=[ROOT])


def asset(item, url, path, *, gallery=False, **signals):
    with Image.open(path) as image:
        width, height = image.size
    return ImageCandidate(news_id=item.id, image_url=url, source_url=PAGE,
        source_type=SourceType.OFFICIAL_SITE, fetched_at=datetime.now(timezone.utc),
        width=width, height=height, original_width=width, original_height=height,
        original_path=str(path), local_path=str(path), downloadable=True,
        original_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        perceptual_hash="0000123400001234",
        signals={"root_source_url": ROOT, "gallery_path": gallery,
                 "page_title": "GALLERY | Studio 15th Project | ワンナイトアフター",
                 **signals})


def scene(tmp_path, name, size):
    image = Image.new("RGB", (1600, 900), "#cee6f8")
    draw = ImageDraw.Draw(image)
    draw.ellipse((180, 90, 900, 820), fill="#bb698c")
    draw.rectangle((980, 430, 1490, 840), fill="#526b93")
    for x in range(100, 1600, 55):
        draw.line((x, 0, x, 900), fill="#eeeaaa", width=4)
    path = tmp_path / name
    image.resize(size, Image.Resampling.LANCZOS).save(path)
    return path


def test_old_full_image_inherits_verified_gallery_evidence_and_beats_thumb(tmp_path):
    item = news()
    thumb = asset(item, PAGE + "thumb/scene01.png", scene(tmp_path, "thumb.png", (600, 338)),
                  gallery=True, visible_detail_score=0.4)
    full = asset(item, PAGE + "full/scene01.png", scene(tmp_path, "full.png", (1600, 900)),
                 entity_conflict="Studio,15th,ワンナイトアフター", auto_select=False,
                 duplicate_of="obsolete", visible_detail_score=0.8)
    result = ImageCurator().curate(Issue(issue_id="262", input_path="fixture.docx", news_items=[item]), [thumb, full])
    assert full.selected
    assert full.image_type is ImageType.GAME_CG
    assert full.image_type_confidence == 0.92
    assert full.signals["gallery_evidence_from"] == thumb.id
    assert full.signals["entity_match"] is True
    assert not thumb.selected
    assert thumb.signals["duplicate_of"] == full.id
    assert "duplicate_of_better_candidate" in thumb.selection_reasons
    assert result.selection_shortfall >= 0


def test_same_filename_and_phash_cannot_transfer_evidence_between_different_scenes(tmp_path):
    item = news()
    thumb = asset(item, PAGE + "thumb/scene01.png", scene(tmp_path, "thumb.png", (600, 338)), gallery=True)
    other_path = tmp_path / "other.png"
    Image.new("RGB", (1600, 900), "#110000").save(other_path)
    other = asset(item, PAGE + "full/scene01.png", other_path)
    ImageCurator().curate(Issue(issue_id="262", input_path="fixture.docx", news_items=[item]), [thumb, other])
    assert other.signals.get("gallery_path") is not True
    assert not other.selected
    assert other.signals.get("duplicate_of") is None


def test_gallery_evidence_does_not_cross_news_or_source(tmp_path):
    first = news()
    second = news("《Other Game》CG更新")
    thumb = asset(first, PAGE + "thumb/01.png", scene(tmp_path, "thumb.png", (600, 338)), gallery=True)
    full = asset(second, PAGE + "full/01.png", scene(tmp_path, "full.png", (1600, 900)))
    other_source = asset(first, PAGE + "full/02.png", tmp_path / "full.png")
    other_source.source_url = ROOT + "special/"
    ImageCurator().curate(Issue(issue_id="262", input_path="fixture.docx", news_items=[first, second]), [thumb, full, other_source])
    assert not full.signals.get("gallery_path")
    assert not other_source.signals.get("gallery_path")


def test_page_title_goods_character_words_do_not_override_local_cg(tmp_path):
    item = news()
    cg = asset(item, PAGE + "full/01.png", scene(tmp_path, "full.png", (1600, 900)), gallery=True)
    cg.signals["page_title"] = "GALLERY | One Night After | CHARACTER GOODS"
    assert ImageTypeClassifier().classify(item, cg).image_type is ImageType.GAME_CG


def test_explicit_other_work_still_blocks_linked_gallery(tmp_path):
    item = news()
    cg = asset(item, PAGE + "full/01.png", scene(tmp_path, "full.png", (1600, 900)),
               gallery=True, other_game_name="Another Work")
    ImageCurator().curate(Issue(issue_id="262", input_path="fixture.docx", news_items=[item]), [cg])
    assert not cg.selected
    assert "entity_conflict" in cg.selection_reasons


def test_share_logo_and_character_are_never_promoted_by_gallery_page(tmp_path):
    item = news()
    path = scene(tmp_path, "sample.png", (1600, 900))
    for name in ("ogp.png", "logo.png", "character01.png", "goods01.png"):
        cg = asset(item, PAGE + name, path, gallery=True)
        assert ImageTypeClassifier().classify(item, cg).image_type is not ImageType.GAME_CG


def test_conflicting_donor_cannot_promote_pixel_identical_full_image(tmp_path):
    item = news()
    thumb = asset(item, PAGE + "thumb/scene.png", scene(tmp_path, "thumb.png", (600, 338)),
                  gallery=True, other_game_name="Another Work")
    full = asset(item, PAGE + "full/scene.png", scene(tmp_path, "full.png", (1600, 900)))
    ImageCurator().curate(Issue(issue_id="262", input_path="fixture.docx", news_items=[item]), [thumb, full])
    assert not thumb.selected
    assert not full.selected
    assert not full.signals.get("gallery_path")


def test_replay_recomputes_inherited_evidence_when_donor_is_absent(tmp_path):
    item = news()
    thumb = asset(item, PAGE + "thumb/scene.png", scene(tmp_path, "thumb.png", (600, 338)), gallery=True)
    full = asset(item, PAGE + "full/scene.png", scene(tmp_path, "full.png", (1600, 900)))
    issue = Issue(issue_id="262", input_path="fixture.docx", news_items=[item])
    curator = ImageCurator()
    curator.curate(issue, [thumb, full])
    assert full.selected
    curator.curate(issue, [full])
    assert not full.selected
    assert not full.signals.get("gallery_path")
    assert full.signals.get("gallery_evidence_from") is None


def test_nested_other_product_path_does_not_inherit_work_identity(tmp_path):
    item = news()
    cg = asset(item, PAGE + "other.png", scene(tmp_path, "full.png", (1600, 900)), gallery=True)
    cg.source_url = ROOT + "related/another-game/gallery.html"
    cg.signals["page_title"] = "Another Game CG Gallery"
    ImageCurator().curate(Issue(issue_id="262", input_path="fixture.docx", news_items=[item]), [cg])
    assert not cg.selected
    assert cg.signals["entity_match"] is False


def test_small_expression_change_is_not_hidden_by_shared_background(tmp_path):
    from galgame_news.curation.dedup import _confirmed_visual_duplicate
    item = news()
    path = scene(tmp_path, "first.png", (1600, 900))
    altered = tmp_path / "expression.png"
    with Image.open(path) as image:
        ImageDraw.Draw(image).rectangle((460, 245, 570, 320), fill="#1c1c2c")
        image.save(altered)
    first = asset(item, PAGE + "scene01.png", path, gallery=True)
    second = asset(item, PAGE + "scene02.png", altered, gallery=True)
    assert not _confirmed_visual_duplicate(first, second)


def test_malformed_previous_recovery_record_is_not_trusted(tmp_path):
    item = news()
    cg = asset(item, PAGE + "full/01.png", scene(tmp_path, "full.png", (1600, 900)), gallery=True,
               gallery_recovery_original="not JSON", gallery_evidence_from="missing-donor")
    ImageCurator().curate(Issue(issue_id="262", input_path="fixture.docx", news_items=[item]), [cg])
    assert not cg.signals.get("gallery_path")
    assert not cg.selected


def test_localized_work_title_without_gallery_evidence_is_review_not_entity_conflict(tmp_path):
    item = news("《One Night After》CG更新")
    cg = asset(item, PAGE + "full/scene01.png", scene(tmp_path, "full.png", (1600, 900)))
    ImageCurator().curate(Issue(issue_id="262", input_path="fixture.docx", news_items=[item]), [cg])
    assert cg.signals["entity_match"] is True
    assert not cg.selected
    assert "entity_conflict" not in cg.selection_reasons
    assert "type_evidence_insufficient" in cg.selection_reasons


def test_work_link_does_not_discard_stronger_named_page_evidence(tmp_path):
    from galgame_news.curation.entity_matching import EntityMatcher
    item = news()
    cg = asset(item, ROOT + "goods01.png", scene(tmp_path, "full.png", (1600, 900)))
    cg.signals["page_title"] = "One Night After goods"
    match = EntityMatcher().match(item, cg)
    assert match.matched is True
    assert "entity_name_in_context" in match.supporting_signals
    assert "page_context" in match.supporting_signals
