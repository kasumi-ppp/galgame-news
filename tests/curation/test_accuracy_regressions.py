from datetime import datetime, timezone
import json
from pathlib import Path

from galgame_news.config import load_config
from galgame_news.curation.curator import ImageCurator
from galgame_news.domain import EventType, ImageNeed, ImageCandidate, Issue, NewsItem, SourceType


NOW = datetime.now(timezone.utc)


def _news(*, issue_id, title, game_names, source_urls=(), event_type=EventType.UNKNOWN, image_need=ImageNeed.EXPLICIT_NEW_IMAGE):
    return NewsItem(
        issue_id=issue_id,
        sequence=1,
        section="新作",
        title=title,
        body="官方公开相关信息",
        game_names=list(game_names),
        source_urls=list(source_urls),
        event_type=event_type,
        image_need=image_need,
    )


def _candidate(news, image_url, *, source_url, source_type, signals=None):
    return ImageCandidate(
        news_id=news.id,
        image_url=image_url,
        source_url=source_url,
        source_type=source_type,
        fetched_at=NOW,
        width=1280,
        height=720,
        downloadable=True,
        signals=signals or {},
    )


def test_246_nine_project_image_is_rejected_for_magarumina():
    news = _news(
        issue_id="246",
        title="《マガルミナ》新作情報",
        game_names=["マガルミナ"],
        source_urls=["https://9-nine-project.com/"],
    )
    wrong = _candidate(
        news,
        "https://9-nine-project.com/assets/9-nine/cg01.jpg",
        source_url="https://9-nine-project.com/",
        source_type=SourceType.OFFICIAL_SITE,
        signals={"page_title": "9-nine 官方网站"},
    )
    result = ImageCurator(load_config()).curate(Issue(issue_id="246", input_path="246.docx", news_items=[news]), [wrong])
    assert wrong.signals["entity_match"] is False
    assert wrong.selected is False
    assert any(f.code == "entity_mismatch" for f in result.failures)


def test_246_nine_project_conflict_is_detected_without_page_title():
    news = _news(issue_id="246", title="《マガルミナ》新作信息", game_names=["マガルミナ"])
    candidate = _candidate(
        news,
        "https://9-nine-project.com/assets/cg01.jpg",
        source_url="https://9-nine-project.com/",
        source_type=SourceType.OFFICIAL_SITE,
    )
    result = ImageCurator(load_config()).curate(Issue(issue_id="246", input_path="246.docx", news_items=[news]), [candidate])
    assert candidate.signals["entity_match"] is False
    assert candidate.selected is False
    assert any(f.code == "entity_mismatch" for f in result.failures)


def test_phantom_animation_cover_from_cbr_is_not_selected():
    news = _news(
        issue_id="259",
        title="《PHANTOM OF INFERNO》新图",
        game_names=["PHANTOM OF INFERNO", "ファントム オブ インフェルノ"],
    )
    cbr = _candidate(
        news,
        "https://static0.cbrimages.com/phantom-anime-cover-art.jpg",
        source_url="https://www.cbrimages.com/anime/phantom",
        source_type=SourceType.UNVERIFIED,
        signals={"page_title": "PHANTOM anime cover art"},
    )
    result = ImageCurator(load_config()).curate(Issue(issue_id="259", input_path="259.docx", news_items=[news]), [cbr])
    assert cbr.selected is False
    assert any(f.code == "entity_mismatch" for f in result.failures)


def test_rainneko_goods_news_rejects_old_cg_and_key_visual():
    news = _news(
        issue_id="259",
        title="RainNeko手办发售",
        game_names=["RainNeko"],
        event_type=EventType.GOODS,
        image_need=ImageNeed.UNKNOWN,
    )
    old_cg = _candidate(
        news,
        "https://entergram.co.jp/oldgame/gallery/cg01.jpg",
        source_url="https://entergram.co.jp/oldgame/gallery",
        source_type=SourceType.OFFICIAL_SITE,
        signals={"page_title": "Other Game old goods"},
    )
    old_kv = _candidate(
        news,
        "https://entergram.co.jp/oldgame/keyvisual_main.jpg",
        source_url="https://entergram.co.jp/oldgame/",
        source_type=SourceType.OFFICIAL_SITE,
    )
    result = ImageCurator(load_config()).curate(Issue(issue_id="259", input_path="259.docx", news_items=[news]), [old_cg, old_kv])
    assert all(value.selected is False for value in (old_cg, old_kv))
    assert any(f.code == "entity_mismatch" for f in result.failures)


def test_259_accuracy_fixture_covers_seven_news_without_network():
    fixture = json.loads((Path(__file__).parents[1] / "fixtures" / "accuracy_259.json").read_text(encoding="utf-8"))
    goods_keys = {"ousama", "rainneko", "nekopara", "cuffs"}
    for entry in fixture["news"]:
        event_type = EventType.GOODS if entry["key"] in goods_keys else EventType.UNKNOWN
        need = ImageNeed.UNKNOWN if entry["key"] in goods_keys else ImageNeed.EXPLICIT_NEW_IMAGE
        news = _news(issue_id=fixture["issue_id"], title=entry["title"], game_names=entry["game_names"], source_urls=entry["source_urls"], event_type=event_type, image_need=need)
        candidates = [
            _candidate(news, value["url"], source_url=value["source_url"], source_type=SourceType(value["source_type"]), signals=value.get("signals"))
            for value in entry["candidates"]
        ]
        if entry.get("expand_candidates"):
            for index in range(len(candidates) + 1, int(entry["expand_candidates"]) + 1):
                candidates.append(_candidate(news, f"https://cuffs.example/event/goods{index:02d}.jpg", source_url="https://cuffs.example/event/", source_type=SourceType.OFFICIAL_SITE, signals={"page_title": "CUFFS event goods"}))
        result = ImageCurator(load_config()).curate(Issue(issue_id=fixture["issue_id"], input_path="fixture.docx", news_items=[news]), candidates)
        if entry["key"] == "cuffs":
            assert len(candidates) == 58
            assert sum(value.selected for value in candidates) <= 10
            assert len(result.candidates) == 58
        if entry["key"] == "ousama":
            from galgame_news.curation.validation import placeholder_asset_reason
            assert placeholder_asset_reason(candidates[0]) == "placeholder_image"
        if entry["key"] in {"yuriarashi", "phantom", "nekopara"}:
            assert any(value.selected for value in candidates if "other" not in value.image_url.casefold() and "cbrimages" not in value.image_url)
        assert all(value.selected is False for value in candidates if "other" in value.image_url.casefold() or "cbrimages" in value.image_url)


def test_third_party_same_title_movie_page_is_not_auto_selected_as_game_media():
    news = _news(
        issue_id="254",
        title="《Summer Snow.》Steam商店页面公开",
        game_names=["Summer Snow."],
        source_urls=["https://store.steampowered.com/app/4933880/Summer_Snow/"],
    )
    movie = _candidate(
        news,
        "https://www.tvguide.com/a/img/hub/screen-shot.png",
        source_url="https://www.tvguide.com/movies/summer-snow/2000206522/",
        source_type=SourceType.UNVERIFIED,
        signals={"page_title": "Summer Snow - Where to Watch and Stream - TV Guide"},
    )

    result = ImageCurator(load_config()).curate(Issue(issue_id="254", input_path="254.docx", news_items=[news]), [movie])

    assert movie.selected is False
    assert any(f.code in {"entity_mismatch", "entity_unverified"} for f in result.failures)


def test_store_page_rejects_image_with_conflicting_named_product_slug():
    news = _news(
        issue_id="254",
        title="《魔卡魅恋！》快闪店活动",
        game_names=["魔卡魅恋！", "Magical Charming"],
        source_urls=["https://store.hikarifield.co.jp/shop/magical_charming"],
        event_type=EventType.GOODS,
        image_need=ImageNeed.UNKNOWN,
    )
    wrong = _candidate(
        news,
        "https://static.hikarifield.co.jp/images/visual/540/tayutama2.jpg",
        source_url="https://store.hikarifield.co.jp/shop/magical_charming",
        source_type=SourceType.UNVERIFIED,
        signals={"page_title": "HIKARI FIELD STORE | 魔卡魅恋！"},
    )

    result = ImageCurator(load_config()).curate(Issue(issue_id="254", input_path="254.docx", news_items=[news]), [wrong])

    assert wrong.signals["entity_match"] is False
    assert wrong.selected is False
    assert any(f.code == "entity_mismatch" for f in result.failures)


def test_third_party_folklore_page_sharing_game_name_is_not_auto_selected():
    news = _news(
        issue_id="254",
        title="《anemoi》OST正式发售",
        game_names=["anemoi"],
        source_urls=["https://key.visualarts.gr.jp/anemoi/"],
        event_type=EventType.GOODS,
        image_need=ImageNeed.UNKNOWN,
    )
    unrelated = _candidate(
        news,
        "https://i0.wp.com/example.invalid/wp-content/uploads/10.png",
        source_url="https://example.invalid/folklore-legends/the-anemoi-of-march/",
        source_type=SourceType.UNVERIFIED,
        signals={
            "page_title": "The Anemoi of March - Example Author",
            "alt": "outside movie screen",
        },
    )

    result = ImageCurator(load_config()).curate(
        Issue(issue_id="254", input_path="254.docx", news_items=[news]),
        [unrelated],
    )

    assert unrelated.signals["entity_match"] is False
    assert unrelated.selected is False
    assert any(f.code == "entity_mismatch" for f in result.failures)
