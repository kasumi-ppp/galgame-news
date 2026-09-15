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


def test_unknown_requires_manual_review():
    item = make_news(title="新情报", body="官方发布了新情报。")
    result = classify("https://cdn.example/assets/asset-01.jpg", news=item, source_url="https://official.example/news")
    decision = evaluate(item, result)

    assert result.image_type is ImageType.UNKNOWN
    assert decision.accepted
    assert decision.requires_review


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
    assert by_name["ss_01.jpg"].selected
    assert by_name["keyvisual_main.jpg"].image_type is ImageType.KEY_VISUAL
    assert by_name["keyvisual_main.jpg"].signals["fallback_only"] is True
    assert "goods_tapestry.jpg" not in selected_names
    assert "logo.png" not in selected_names
    assert "bnr_main.jpg" not in selected_names
    assert sum(candidate.selected for candidate in result.candidates) == 2
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
