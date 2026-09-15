from datetime import datetime, timezone

from galgame_news.curation.entity_matching import EntityMatcher
from galgame_news.curation.source_policy import SourceTrustPolicy
from galgame_news.domain import ImageCandidate, NewsItem, SourceType


NOW = datetime.now(timezone.utc)


def make_news(*, game_names=None, source_urls=None, organizations=None):
    return NewsItem(
        issue_id="259",
        sequence=1,
        section="新作",
        title="《MONOCHROME SERENADE》CG更新",
        body="官方公开新作图片",
        game_names=game_names or ["MONOCHROME SERENADE", "モノクロームセレナーデ"],
        organizations=organizations or ["Laplacian"],
        source_urls=source_urls or ["https://laplacian.jp/games/mono/"],
    )


def make_candidate(url, *, source_url=None, source_type=SourceType.UNVERIFIED, signals=None):
    return ImageCandidate(
        news_id="placeholder-news-id",
        image_url=url,
        source_url=source_url or url,
        source_type=source_type,
        fetched_at=NOW,
        signals=signals or {},
    )


def test_entity_matching_normalizes_full_width_case_and_decorative_punctuation():
    news = make_news(game_names=["ＭＯＮＯＣＨＲＯＭＥ　ＳＥＲＥＮＡＤＥ"])
    candidate = make_candidate(
        "https://laplacian.jp/games/mono/assets/%E3%80%8CMONochrome-Serenade%E3%80%8D-cg01.jpg",
        source_url="https://laplacian.jp/games/mono/",
        source_type=SourceType.OFFICIAL_SITE,
    )
    result = EntityMatcher().match(news, candidate)
    assert result.matched is True
    assert result.confidence >= 0.8
    assert result.official_domain_match is True


def test_entity_matching_accepts_alias_from_game_names():
    news = make_news(game_names=["モノクロームセレナーデ", "MONOCHROME SERENADE"])
    candidate = make_candidate(
        "https://cdn.example/mono-cg02.jpg",
        signals={"page_title": "モノクロームセレナーデ 公式CG"},
    )
    result = EntityMatcher().match(news, candidate)
    assert result.matched is True
    assert "モノクロームセレナーデ" in result.matched_entities


def test_entity_matching_uses_quoted_title_when_game_names_are_missing():
    news = make_news(game_names=[],)
    news.title = "《Example Game》CG更新"
    candidate = make_candidate(
        "https://cdn.example/example-game-cg01.jpg",
        signals={"page_title": "Example Game CG"},
    )
    result = EntityMatcher().match(news, candidate)
    assert result.matched is True
    assert result.matched_entities


def test_short_name_requires_context():
    news = make_news(game_names=["ONE"])
    no_context = make_candidate("https://cdn.example/one-cg01.jpg")
    strong_context = make_candidate(
        "https://publisher.example/one-cg01.jpg",
        source_url="https://publisher.example/one/",
        signals={"page_title": "ONE 公式サイト", "organization_match": 1.0},
    )
    assert EntityMatcher().match(news, no_context).matched is None
    assert EntityMatcher().match(news, strong_context).matched is True

    official_without_context = make_candidate("https://publisher.example/one-cg01.jpg", source_type=SourceType.OFFICIAL_SITE)
    assert EntityMatcher().match(news, official_without_context).matched is None


def test_same_official_domain_is_high_confidence():
    news = make_news(source_urls=["https://publisher.example/mono/news/cg"])
    candidate = make_candidate(
        "https://publisher.example/mono/gallery/cg01.jpg",
        source_url="https://publisher.example/mono/gallery/",
        source_type=SourceType.OFFICIAL_SITE,
    )
    result = EntityMatcher().match(news, candidate)
    assert result.matched is True
    assert result.official_domain_match is True
    assert result.confidence >= 0.9


def test_steam_app_id_mismatch_is_a_conflict():
    news = make_news(source_urls=["https://store.steampowered.com/app/123456/Mono/"])
    candidate = make_candidate(
        "https://cdn.steamstatic.com/steam/apps/999999/header.jpg",
        source_url="https://store.steampowered.com/app/999999/Other/",
        source_type=SourceType.STEAM,
    )
    result = EntityMatcher().match(news, candidate)
    assert result.matched is False
    assert result.conflicting_entities


def test_matching_steam_app_id_is_strong_entity_evidence():
    news = make_news(source_urls=["https://store.steampowered.com/app/123456/Mono/"])
    candidate = make_candidate(
        "https://cdn.steamstatic.com/steam/apps/123456/header.jpg",
        source_url="https://steamcommunity.com/app/123456/",
        source_type=SourceType.STEAM,
    )
    result = EntityMatcher().match(news, candidate)
    assert result.matched is True
    assert result.confidence >= 0.8
    assert "steam_app_id_match" in result.supporting_signals


def test_third_party_without_entity_evidence_is_unverified():
    news = make_news()
    candidate = make_candidate("https://cbrimages.com/images/cg01.jpg", source_url="https://cbrimages.com/article")
    result = EntityMatcher().match(news, candidate)
    assert result.matched is None
    assert result.confidence == 0.0


def test_third_party_strong_entity_evidence_can_match_but_is_not_official():
    news = make_news()
    candidate = make_candidate(
        "https://3dmgame.com/uploads/monochrome-serenade-cg01.jpg",
        source_url="https://3dmgame.com/news/mono",
        signals={"page_title": "MONOCHROME SERENADE CG"},
    )
    result = EntityMatcher().match(news, candidate)
    assert result.matched is True
    assert result.official_domain_match is False
    assert result.confidence >= 0.75


def test_source_policy_does_not_promote_image_proxy_to_official():
    candidate = make_candidate(
        "https://media-amazon.com/images/I/cover.jpg",
        source_url="https://amazon.com/dp/B000000",
    )
    result = SourceTrustPolicy().classify(candidate)
    assert result.tier in {"image_proxy", "third_party_news"}
    assert result.officiality < 0.8
    assert result.is_official is False


def test_source_policy_distinguishes_official_event_and_brand_pages():
    event = make_candidate(
        "https://publisher.example/assets/event.jpg",
        source_url="https://publisher.example/news/event-2026",
        source_type=SourceType.OFFICIAL_SITE,
    )
    brand = make_candidate(
        "https://publisher.example/assets/logo.jpg",
        source_url="https://publisher.example/company/about",
        source_type=SourceType.OFFICIAL_SITE,
    )
    assert SourceTrustPolicy().classify(event).tier == "official_event_page"
    assert SourceTrustPolicy().classify(brand).tier == "official_brand_page"


def test_source_policy_marks_kun_related_proxy_low_trust():
    candidate = make_candidate("https://cdn.kun-gallery.example/cg01.jpg", source_url="https://cdn.kun-gallery.example/article")
    result = SourceTrustPolicy().classify(candidate)
    assert result.tier in {"image_proxy", "third_party_news"}
    assert result.is_official is False


def test_source_policy_classifies_aggregator_as_low_trust():
    candidate = make_candidate(
        "https://fandom.com/wiki/File:game.jpg",
        source_url="https://fandom.com/wiki/Other_Game",
    )
    result = SourceTrustPolicy().classify(candidate)
    assert result.tier == "aggregator"
    assert result.is_official is False


def test_brand_page_without_specific_game_evidence_requires_review():
    from galgame_news.curation.curator import ImageCurator
    from galgame_news.domain import ImageNeed, Issue

    news = make_news(source_urls=["https://publisher.example/company/about"])
    candidate = make_candidate(
        "https://publisher.example/company/brand-visual.jpg",
        source_url="https://publisher.example/company/about",
        source_type=SourceType.OFFICIAL_SITE,
    )
    candidate.news_id = news.id
    result = ImageCurator().curate(Issue(issue_id="259", input_path="fixture.docx", news_items=[news]), [candidate])
    assert candidate.signals["source_tier"] == "official_brand_page"
    assert candidate.selected is False
    assert "uncertain_match" in {reason.value for reason in candidate.review_reasons}


def test_conflicting_game_name_is_rejected():
    news = make_news()
    candidate = make_candidate(
        "https://news.example/images/other-cg01.jpg",
        signals={"page_title": "PHANTOM - unrelated game", "entity_conflict": "PHANTOM"},
    )
    result = EntityMatcher().match(news, candidate)
    assert result.matched is False
    assert "PHANTOM" in result.conflicting_entities


def test_other_cjk_game_title_is_a_conflict_not_an_unknown_match():
    news = make_news(game_names=["9-nine-"])
    candidate = make_candidate(
        "https://news.example/images/cg01.jpg",
        signals={"page_title": "マガルミナ CG"},
    )
    result = EntityMatcher().match(news, candidate)
    assert result.matched is False


def test_entity_exception_can_be_isolated_by_curator(monkeypatch):
    from galgame_news.curation import entity_matching
    from galgame_news.curation.curator import ImageCurator
    from galgame_news.domain import ImageNeed, Issue

    item = make_news()
    item.image_need = ImageNeed.EXPLICIT_NEW_IMAGE
    bad = make_candidate("https://publisher.example/bad-cg.jpg", source_type=SourceType.OFFICIAL_SITE, signals={"cg_match": 1.0})
    good = make_candidate("https://publisher.example/good-cg.jpg", source_type=SourceType.OFFICIAL_SITE, signals={"cg_match": 1.0})
    bad.news_id = good.news_id = item.id
    original = entity_matching.EntityMatcher.match

    def flaky(self, news_item, value):
        if value.image_url.endswith("bad-cg.jpg"):
            raise RuntimeError("fixture entity error")
        return original(self, news_item, value)

    monkeypatch.setattr(entity_matching.EntityMatcher, "match", flaky)
    result = ImageCurator().curate(Issue(issue_id="259", input_path="fixture.docx", news_items=[item]), [bad, good])
    assert good.selected is True
    assert bad.selected is False
    assert any(f.code == "entity_match_error" and f.candidate_id == bad.id for f in result.failures)
