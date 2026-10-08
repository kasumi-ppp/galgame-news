import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from galgame_news.config import load_config
from galgame_news.domain import (
    EventType,
    ImageCandidate,
    ImageNeed,
    ImageType,
    Issue,
    NewsItem,
    ReviewReason,
    SourceType,
)


def make_news(*, title="新CG公开", body="官方公开了一张新CG。", event_type=EventType.UPDATE, image_need=ImageNeed.EXPLICIT_NEW_IMAGE):
    return NewsItem(
        issue_id="252",
        sequence=1,
        section="新作",
        title=title,
        body=body,
        event_type=event_type,
        image_need=image_need,
        game_names=["Example Game"],
        source_urls=["https://official.example/news/cg"],
    )


def make_candidate(url, *, source_url="https://official.example/news/cg", source_type=SourceType.OFFICIAL_SITE, signals=None, width=1280, height=720, news_id=None):
    candidate = ImageCandidate(
        news_id=news_id or make_news().id,
        image_url=url,
        source_url=source_url,
        source_type=source_type,
        fetched_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        width=width,
        height=height,
        downloadable=True,
        signals=signals or {},
    )
    return candidate


def classify(url, *, news=None, source_url="https://official.example/news/cg", source_type=SourceType.OFFICIAL_SITE, signals=None, width=1280, height=720):
    from galgame_news.curation.image_typing import ImageTypeClassifier

    item = news or make_news()
    candidate = make_candidate(url, source_url=source_url, source_type=source_type, signals=signals, width=width, height=height, news_id=item.id)
    return ImageTypeClassifier(load_config().image_types).classify(item, candidate)


def evaluate(news, result):
    from galgame_news.curation.image_typing import ImageRequirementPolicy

    return ImageRequirementPolicy(load_config().image_types).evaluate(news, result)


def test_steam_share_image_is_ui_and_rejected():
    item = make_news()
    result = classify(
        "https://store.akamai.steamstatic.com/public/shared/images/responsive/steam_share_image.jpg",
        news=item,
        source_url="https://store.steampowered.com/app/1/example/",
        source_type=SourceType.STEAM,
    )

    assert result.image_type is ImageType.UI
    assert not evaluate(item, result).accepted


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://official.example/assets/logo.png", ImageType.LOGO),
        ("https://official.example/assets/bnr_main.jpg", ImageType.BANNER),
        ("https://official.example/game/character_heroine.png", ImageType.CHARACTER_ART),
        ("https://official.example/shop/goods_tapestry.jpg", ImageType.GOODS),
    ],
)
def test_high_precision_filename_rules_classify_common_non_cg_assets(url, expected):
    assert classify(url).image_type is expected


def test_gallery_cg_is_classified_as_game_cg():
    result = classify(
        "https://official.example/gallery/cg01.jpg",
        source_url="https://official.example/game/gallery",
        signals={"cg_match": 1.0},
    )

    assert result.image_type is ImageType.GAME_CG
    assert result.confidence >= load_config().image_types.minimum_type_confidence


def test_gallery_page_horizontal_art_is_game_cg_without_extra_adapter_signal():
    result = classify(
        "https://official.example/gallery/image01.jpg",
        source_url="https://official.example/game/gallery",
    )

    assert result.image_type is ImageType.GAME_CG


def test_official_news_linked_gallery_for_cg_update_has_auto_select_confidence():
    root = "https://hook-net.jp/smee/one/"
    item = make_news(title="《One Night After》CG更新", body="官网更新了 CG 图片。")
    item.source_urls = [root]
    result = classify(
        "http://www.hook-net.jp/smee/one/_assets/images/gallery/full/gallery_n_009.png",
        news=item,
        source_url="http://www.hook-net.jp/smee/one/gallery/",
        signals={
            "root_source_url": root,
            "gallery_path": True,
            "page_title": "GALLERY | SMEE 15th Project | ワンナイトアフター - SMEE",
        },
    )

    assert result.image_type is ImageType.GAME_CG
    assert result.confidence >= 0.8
    assert evaluate(item, result).auto_select is True


def test_fragment_gallery_scene_has_auto_select_confidence_but_logo_does_not():
    root = "https://liar.co.jp/tasogare/index.html#GALLERY"
    item = make_news(title="《誰ソ彼のシェイプシフター》CG更新", body="官网更新了 CG。")
    item.source_urls = [root]
    cg = classify(
        "https://liar.co.jp/tasogare/images/sample-cg_01.jpg",
        news=item,
        source_url="https://liar.co.jp/tasogare/index.html",
        signals={"root_source_url": root, "gallery_path": True, "page_title": "誰ソ彼のシェイプシフター"},
    )
    logo = classify(
        "https://liar.co.jp/tasogare/images/logo.png",
        news=item,
        source_url="https://liar.co.jp/tasogare/index.html",
        signals={"root_source_url": root, "gallery_path": False, "page_title": "誰ソ彼のシェイプシフター"},
    )
    assert cg.image_type is ImageType.GAME_CG
    assert cg.confidence >= 0.8
    assert evaluate(item, cg).auto_select is True
    assert logo.image_type is ImageType.LOGO
    assert evaluate(item, logo).accepted is False


def test_publisher_home_gallery_requires_reached_work_page_title():
    item = make_news(title="《花鐘カナデ＊グラム Chapter:4 綾世奏》官网更新", body="官方公布了一张特殊场景CG。")
    item.game_names = ["花鐘カナデ＊グラム Chapter:4 綾世奏"]
    item.source_urls = ["https://nanawind.jp/"]
    signals = {"root_source_url": "https://nanawind.jp/", "gallery_path": True}
    related = classify(
        "https://nanawind.jp/product/prj06/chapter4/images/cg01.jpg",
        news=item,
        source_url="https://nanawind.jp/product/prj06/chapter4/",
        signals={**signals, "page_title": "Chapter:4 | 花鐘カナデ＊グラム"},
    )
    unrelated = classify(
        "https://nanawind.jp/product/prj07/other/images/cg01.jpg",
        news=item,
        source_url="https://nanawind.jp/product/prj07/other/",
        signals={**signals, "page_title": "Other Game"},
    )
    assert related.image_type is ImageType.GAME_CG
    assert related.confidence >= 0.8
    assert unrelated.confidence < 0.8


def test_quiz_promotion_is_not_promoted_to_cg_by_generic_graphic_css():
    item = make_news(title="《花鐘カナデ＊グラム》CG更新", body="官网更新 CG。")
    item.game_names = ["花鐘カナデ＊グラム"]
    item.source_urls = ["https://nanawind.jp/"]
    result = classify(
        "https://nanawind.jp/product/prj06/chapter4/images/quiz.jpg",
        news=item,
        source_url="https://nanawind.jp/product/prj06/chapter4/",
        signals={"root_source_url": "https://nanawind.jp/", "gallery_path": True, "page_title": "花鐘カナデ＊グラム"},
    )
    assert result.image_type is ImageType.ANNOUNCEMENT_ART
    assert evaluate(item, result).accepted is False


def test_game_gallery_cg_under_product_route_is_not_mistaken_for_goods():
    item = make_news(title="《アンスリウム》CG 更新", body="官网更新了一张 CG。")
    item.source_urls = ["https://circus.example/product/clown/anthurium/"]
    result = classify(
        "https://circus.example/product/clown/anthurium/rc/gallery/event-01.jpg",
        news=item,
        source_url="https://circus.example/product/clown/anthurium/",
        signals={
            "page_title": "アンスリウム-i entrust to you-",
            "alt": "イベントCG 1",
            "root_source_url": "https://circus.example/product/clown/anthurium/",
        },
    )

    assert result.image_type is ImageType.GAME_CG
    assert evaluate(item, result).accepted


def test_steam_screenshot_is_gameplay_screenshot_not_store_art():
    result = classify(
        "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/1/ss_01.jpg",
        source_url="https://store.steampowered.com/app/1/example/screenshots/",
        source_type=SourceType.STEAM,
    )

    assert result.image_type is ImageType.GAMEPLAY_SCREENSHOT


def test_character_signal_has_priority_over_generic_cg_filename():
    result = classify("https://official.example/assets/character_cg.png")

    assert result.image_type is ImageType.CHARACTER_ART


def test_explicit_character_and_cover_semantics_override_tall_aspect_banner_hint():
    character = classify("https://official.example/assets/character_heroine.png", width=300, height=1000)
    cover = classify("https://official.example/package/jacket.jpg", width=300, height=1000)

    assert character.image_type is ImageType.CHARACTER_ART
    assert cover.image_type is ImageType.COVER


def test_steam_capsule_is_cover_not_goods_or_gameplay():
    result = classify(
        "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/1/capsule_616x353.jpg",
        source_url="https://store.steampowered.com/app/1/example/",
        source_type=SourceType.STEAM,
    )

    assert result.image_type is ImageType.COVER


def test_gift_news_allows_announcement_art():
    item = make_news(title="周年纪念贺图公开", body="官方发布了纪念图。")
    result = classify(
        "https://official.example/news/announcement/commemorative_01.jpg",
        news=item,
        source_url="https://official.example/news/announcement",
    )

    decision = evaluate(item, result)
    assert result.image_type is ImageType.ANNOUNCEMENT_ART
    assert decision.accepted


def test_goods_news_allows_goods():
    item = make_news(title="周边商品发售", body="新周边商品公开。", event_type=EventType.GOODS, image_need=ImageNeed.UNKNOWN)
    result = classify("https://official.example/shop/goods_tapestry.jpg", news=item)

    assert evaluate(item, result).accepted


def test_cg_news_accepts_game_cg_and_rejects_non_visual_chrome_and_products():
    item = make_news()
    accepted = evaluate(item, classify("https://official.example/gallery/cg01.jpg", news=item, source_url="https://official.example/gallery"))
    assert accepted.accepted
    assert not accepted.fallback_only

    for image_type, url in [
        (ImageType.GOODS, "https://official.example/shop/goods_01.jpg"),
        (ImageType.LOGO, "https://official.example/assets/logo.png"),
        (ImageType.BANNER, "https://official.example/assets/bnr_main.jpg"),
        (ImageType.UI, "https://official.example/assets/button.png"),
        (ImageType.PHOTO, "https://official.example/news/photo_01.jpg"),
        (ImageType.COVER, "https://official.example/package/cover.jpg"),
    ]:
        result = classify(url, news=item)
        assert result.image_type is image_type
        decision = evaluate(item, result)
        assert not decision.accepted
        assert decision.rejection_reason


def test_cg_update_with_release_date_still_rejects_character_art():
    item = make_news(
        title="《Sweet Starlight Sisters》CG更新",
        body="官方公开一张游戏 CG，预计明年发售。",
        event_type=EventType.RELEASE,
    )
    character = classify("https://official.example/game/character201.png", news=item)
    assert character.image_type is ImageType.CHARACTER_ART
    assert evaluate(item, character).accepted is False


def test_cg_news_only_allows_key_visual_as_fallback():
    item = make_news()
    result = classify("https://official.example/assets/keyvisual_main.jpg", news=item)
    decision = evaluate(item, result)

    assert result.image_type is ImageType.KEY_VISUAL
    assert decision.accepted
    assert decision.fallback_only


def test_announcement_art_is_not_silently_accepted_as_cg():
    item = make_news()
    result = classify("https://official.example/news/announcement/announcement_art.jpg", news=item)

    assert result.image_type is ImageType.ANNOUNCEMENT_ART
    assert not evaluate(item, result).accepted


def test_anniversary_goods_news_keeps_goods_as_a_valid_intent():
    item = make_news(
        title="Example Game 10th Anniversary goods collection",
        body="New goods and commemorative items are now available.",
        event_type=EventType.GOODS,
    )
    goods = classify("https://official.example/shop/tapestry.jpg", news=item)
    assert goods.image_type is ImageType.GOODS
    decision = evaluate(item, goods)
    assert decision.accepted
    assert decision.type_match == 1.0


def test_gallery_and_wide_aspect_are_weak_cg_evidence():
    item = make_news()
    result = classify("https://official.example/gallery/image-01.jpg", news=item, width=1800, height=600)
    assert result.image_type is ImageType.GAME_CG
    assert result.confidence < 0.8
    decision = evaluate(item, result)
    assert decision.requires_review
    assert decision.auto_select is False


def test_unknown_requires_manual_review():
    item = make_news(title="新情报", body="官方发布了新情报。")
    result = classify("https://cdn.example/assets/asset-01.jpg", news=item, source_url="https://official.example/news")
    decision = evaluate(item, result)

    assert result.image_type is ImageType.UNKNOWN
    assert decision.accepted
    assert decision.requires_review


def test_explicit_cg_unknown_is_retained_but_never_auto_selected():
    from galgame_news.curation.curator import ImageCurator

    item = make_news()
    unknown = make_candidate("https://official.example/assets/asset-01.jpg", news_id=item.id, signals={"page_title": "Example Game official"})
    issue = Issue(issue_id="252", input_path="252.docx", news_items=[item])
    result = ImageCurator(load_config()).curate(issue, [unknown])

    assert result.candidates == [unknown]
    assert unknown.image_type is ImageType.UNKNOWN
    assert unknown.selected is False
    assert ReviewReason.IMAGE_TYPE_REVIEW in unknown.review_reasons


def test_animated_image_is_retained_for_review_but_never_automatically_selected():
    from galgame_news.curation.curator import ImageCurator

    item = make_news()
    animated = make_candidate(
        "https://official.example/gallery/event01.gif",
        news_id=item.id,
        signals={"page_title": "Example Game event CG", "animated_source": True, "game_match": 1.0},
    )
    result = ImageCurator(load_config()).curate(Issue(issue_id="252", input_path="252.docx", news_items=[item]), [animated])
    assert animated in result.candidates
    assert animated.selected is False
    assert animated.signals["auto_select"] is False
    assert "animated_source_requires_review" in animated.selection_reasons


def test_cg_key_visual_is_fallback_only_when_no_preferred_type_exists():
    from galgame_news.curation.curator import ImageCurator

    item = make_news()
    key_visual = make_candidate("https://official.example/assets/keyvisual_main.jpg", news_id=item.id, signals={"page_title": "Example Game official"})
    game_cg = make_candidate("https://official.example/gallery/cg01.jpg", source_url="https://official.example/gallery", news_id=item.id, signals={"cg_match": 1.0, "page_title": "Example Game CG", "root_source_url": item.source_urls[0]})
    issue = Issue(issue_id="252", input_path="252.docx", news_items=[item])

    with_preferred = ImageCurator(load_config()).curate(issue, [key_visual, game_cg])
    assert game_cg.selected is True
    assert key_visual.selected is False

    only_fallback = make_candidate("https://official.example/assets/keyvisual_only.jpg", news_id=item.id, signals={"page_title": "Example Game official"})
    result = ImageCurator(load_config()).curate(issue, [only_fallback])
    assert only_fallback.selected is True
    assert result.selection_shortfall == 4


def test_generic_and_unknown_requirements_do_not_auto_select_unknown_by_default():
    from galgame_news.curation.curator import ImageCurator

    item = make_news(title="新情报", body="官方发布了新情报。", image_need=ImageNeed.UNKNOWN)
    unknown = make_candidate("https://official.example/assets/asset-01.jpg", news_id=item.id, signals={"page_title": "Example Game official"})
    result = ImageCurator(load_config()).curate(Issue(issue_id="252", input_path="252.docx", news_items=[item]), [unknown])

    assert unknown.image_type is ImageType.UNKNOWN
    assert unknown.selected is False
    assert result.selection_shortfall == 5


def test_unknown_auto_selection_can_be_enabled_by_toml_override(tmp_path):
    from galgame_news.curation.curator import ImageCurator

    config_path = tmp_path / "override.toml"
    config_path.write_text("[image_types]\nauto_select_unknown = true\nmax_unknown_per_news = 1\n", encoding="utf-8")
    config = load_config(config_path)
    item = make_news(title="新情报", body="官方发布了新情报。", image_need=ImageNeed.UNKNOWN)
    unknown = make_candidate("https://official.example/assets/asset-01.jpg", news_id=item.id, signals={"page_title": "Example Game official"})
    result = ImageCurator(config).curate(Issue(issue_id="252", input_path="252.docx", news_items=[item]), [unknown])

    assert unknown.selected is True
    assert result.selection_shortfall == 4


def test_classifier_and_ranker_are_repeatable_for_same_input():
    from galgame_news.curation.ranker import ImageRanker

    item = make_news()
    first = [make_candidate("https://official.example/gallery/cg01.jpg", source_url="https://official.example/gallery", signals={"cg_match": 1.0}, news_id=item.id), make_candidate("https://official.example/assets/keyvisual.jpg", news_id=item.id)]
    second = [candidate.model_copy(deep=True) for candidate in first]
    classifier = __import__("galgame_news.curation.image_typing", fromlist=["ImageTypeClassifier"]).ImageTypeClassifier(load_config().image_types)
    for values in (first, second):
        for candidate in values:
            typed = classifier.classify(item, candidate)
            candidate.image_type = typed.image_type
            candidate.image_type_confidence = typed.confidence
            candidate.signals["type_match"] = 1.0 if typed.image_type is ImageType.GAME_CG else 0.3
    ranked_first = ImageRanker(load_config().scoring, load_config().image_types).rank(item, first)
    ranked_second = ImageRanker(load_config().scoring, load_config().image_types).rank(item, second)

    assert [candidate.id for candidate in ranked_first] == [candidate.id for candidate in ranked_second]
    assert [candidate.score.model_dump() for candidate in ranked_first] == [candidate.score.model_dump() for candidate in ranked_second]


def test_ranker_uses_public_image_type_when_type_signal_is_absent():
    from galgame_news.curation.ranker import ImageRanker

    item = make_news()
    cg = make_candidate("https://official.example/gallery/cg01.jpg", news_id=item.id)
    cg.image_type = ImageType.GAME_CG
    key_visual = make_candidate("https://official.example/keyvisual.jpg", news_id=item.id)
    key_visual.image_type = ImageType.KEY_VISUAL

    ranked = ImageRanker(load_config().scoring, load_config().image_types).rank(item, [key_visual, cg])

    assert ranked[0] is cg
    assert cg.score.type_match > key_visual.score.type_match


def test_ranker_is_deterministic_for_dated_candidates():
    from galgame_news.curation.ranker import ImageRanker

    item = make_news()
    candidate = make_candidate("https://official.example/gallery/cg01.jpg", news_id=item.id)
    candidate.image_type = ImageType.GAME_CG
    candidate.published_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    ranker = ImageRanker(load_config().scoring, load_config().image_types)

    first = ranker.rank(item, [candidate.model_copy(deep=True)])[0].score.model_dump()
    second = ranker.rank(item, [candidate.model_copy(deep=True)])[0].score.model_dump()

    assert first == second


def test_old_image_candidate_json_without_new_fields_still_parses():
    payload = {
        "news_id": "n1",
        "image_url": "https://official.example/cg/01.jpg",
        "source_url": "https://official.example/news/1",
        "fetched_at": "2026-09-01T00:00:00Z",
    }
    candidate = ImageCandidate.model_validate(payload)

    assert candidate.image_type is ImageType.UNKNOWN
    assert candidate.image_type_confidence == 0.0


def test_classifier_exception_only_isolates_current_candidate(monkeypatch):
    from galgame_news.curation import image_typing
    from galgame_news.curation.curator import ImageCurator

    item = make_news()
    bad = make_candidate("https://official.example/bad.jpg", news_id=item.id)
    good = make_candidate("https://official.example/gallery/cg01.jpg", source_url="https://official.example/gallery", signals={"cg_match": 1.0}, news_id=item.id)
    original = image_typing.ImageTypeClassifier.classify

    def flaky(self, news, candidate):
        if candidate.image_url.endswith("bad.jpg"):
            raise RuntimeError("fixture classifier error")
        return original(self, news, candidate)

    monkeypatch.setattr(image_typing.ImageTypeClassifier, "classify", flaky)
    result = ImageCurator(load_config()).curate(Issue(issue_id="252", input_path="252.docx", news_items=[item]), [bad, good])

    assert {candidate.id for candidate in result.candidates} == {bad.id, good.id}
    assert any(f.code == "image_type_error" and f.candidate_id == bad.id for f in result.failures)
    assert good.image_type is ImageType.GAME_CG


def test_252_offline_fixture_keeps_screenshot_and_rejects_chrome_and_goods():
    fixture_path = Path(__file__).parents[1] / "fixtures" / "image_typing_252.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    item = make_news(title=fixture["news"]["title"], body=fixture["news"]["body"])
    candidates = [
        make_candidate(
            entry["image_url"],
            source_url=entry["source_url"],
            source_type=SourceType(entry.get("source_type", "official_site")),
            signals=entry.get("signals"),
            news_id=item.id,
        )
        for entry in fixture["candidates"]
    ]

    from galgame_news.curation.curator import ImageCurator

    result = ImageCurator(load_config()).curate(Issue(issue_id="252", input_path="252.docx", news_items=[item]), candidates)
    by_name = {candidate.image_url.rsplit("/", 1)[-1]: candidate for candidate in candidates}
    selected_names = {candidate.image_url.rsplit("/", 1)[-1] for candidate in result.candidates if candidate.selected}

    assert by_name["steam_share_image.jpg"].image_type is ImageType.UI
    assert "steam_share_image.jpg" not in selected_names
    assert by_name["ss_01.jpg"].image_type is ImageType.GAMEPLAY_SCREENSHOT
    assert by_name["ss_01.jpg"].selected is False
    assert by_name["ss_01.jpg"].signals["source_linked"] is False
    assert by_name["keyvisual_main.jpg"].image_type is ImageType.KEY_VISUAL
    assert by_name["keyvisual_main.jpg"].signals["fallback_only"] is True
    assert "goods_tapestry.jpg" not in selected_names
    assert "logo.png" not in selected_names
    assert "bnr_main.jpg" not in selected_names
    assert not by_name["keyvisual_main.jpg"].selected
    assert sum(candidate.selected for candidate in result.candidates) == 0
    assert any(f.code == "image_type_rejected" and "goods" in f.message for f in result.failures)


def test_curator_writes_type_fields_and_rejection_failure_for_hard_rejects():
    from galgame_news.curation.curator import ImageCurator

    item = make_news()
    logo = make_candidate("https://official.example/assets/logo.png", news_id=item.id)
    result = ImageCurator(load_config()).curate(Issue(issue_id="252", input_path="252.docx", news_items=[item]), [logo])

    assert logo.image_type is ImageType.LOGO
    assert logo.image_type_confidence > 0
    assert not logo.selected
    assert any(f.code == "image_type_rejected" and "logo" in f.message for f in result.failures)


def test_output_index_includes_filtered_candidates_with_type_fields(tmp_path):
    from galgame_news.curation.curator import ImageCurator
    from galgame_news.delivery.output import OutputManager
    from galgame_news.domain import PipelineResult

    item = make_news()
    logo = make_candidate("https://official.example/assets/logo.png", news_id=item.id)
    curated = ImageCurator(load_config()).curate(Issue(issue_id="252", input_path="252.docx", news_items=[item]), [logo])
    OutputManager().write(
        PipelineResult(issue=Issue(issue_id="252", input_path="252.docx", news_items=[item]), candidates=curated.candidates, filtered_candidates=curated.filtered_candidates, failures=curated.failures),
        tmp_path,
    )

    payload = json.loads((tmp_path / "image_index.json").read_text(encoding="utf-8"))
    saved = next(candidate for candidate in payload["candidates"] if candidate["id"] == logo.id)
    assert saved["image_type"] == "logo"
    assert saved["image_type_confidence"] > 0
    assert saved["selected"] is False
