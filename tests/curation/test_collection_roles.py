from datetime import datetime, timezone

from galgame_news.config import load_config
from galgame_news.curation.allocator import ImageAllocator
from galgame_news.curation.entity_matching import EntityMatcher
from galgame_news.curation.image_typing import ImageRequirementPolicy, ImageTypeClassifier
from galgame_news.domain import (
    EventType,
    ImageCandidate,
    ImageEvidence,
    ImageNeed,
    ImageType,
    Issue,
    NewsItem,
    SourceType,
)


NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)


def make_news(*, title="《Example Game》新CG公开", event_type=EventType.UPDATE, game_names=None, source_urls=None):
    return NewsItem(
        issue_id="263",
        sequence=1,
        section="新作",
        title=title,
        body="官方更新内容",
        event_type=event_type,
        image_need=ImageNeed.EXPLICIT_NEW_IMAGE,
        game_names=game_names if game_names is not None else ["Example Game"],
        organizations=["Example Brand"],
        source_urls=source_urls or ["https://official.example/news/update"],
    )


def make_candidate(news, *, url="https://official.example/news/update/assets/image.jpg", page="https://official.example/news/update/gallery", role=None, item_id="", container="", relationship="image attribute", text="", source_type=SourceType.OFFICIAL_SITE, signals=None):
    evidence = []
    if role:
        evidence.append(ImageEvidence(
            page_url=page,
            container=container,
            item_id=item_id,
            role=role,
            text=text,
            method="dom",
            relationship=relationship,
        ))
    return ImageCandidate(
        news_id=news.id,
        image_url=url,
        source_url=page,
        source_type=source_type,
        fetched_at=NOW,
        width=1280,
        height=720,
        downloadable=True,
        evidence=evidence,
        signals=signals or {},
    )


def test_background_role_requires_news_linked_official_page_and_filename_is_not_enough():
    news = make_news()
    linked = make_candidate(
        news,
        url="https://official.example/news/update/assets/bg_01.jpg",
        role="background_art",
        page="https://official.example/news/update/gallery",
        signals={"root_source_url": news.source_urls[0]},
    )
    unlinked = make_candidate(
        news,
        url="https://official.example/assets/bg_01.jpg",
        page="https://official.example/other-work/gallery",
        role="background_art",
        signals={"root_source_url": "https://official.example/other-work/"},
    )
    filename_only = make_candidate(news, url="https://official.example/news/update/assets/bg_02.jpg")
    classifier = ImageTypeClassifier(load_config().image_types)

    assert classifier.classify(news, linked).image_type is ImageType.BACKGROUND_ART
    assert classifier.classify(news, linked).confidence == 0.92
    assert classifier.classify(news, unlinked).image_type is ImageType.UNKNOWN
    assert classifier.classify(news, filename_only).image_type is not ImageType.BACKGROUND_ART


def test_decorative_is_retained_with_high_confidence_but_never_auto_selected():
    from galgame_news.curation.curator import ImageCurator

    news = make_news()
    candidate = make_candidate(
        news,
        role="decorative",
        page="https://official.example/news/update/gallery",
        signals={"root_source_url": news.source_urls[0], "page_title": "Example Game Official Gallery"},
    )
    typed = ImageTypeClassifier(load_config().image_types).classify(news, candidate)
    decision = ImageRequirementPolicy(load_config().image_types).evaluate(news, typed)
    result = ImageCurator(load_config()).curate(
        Issue(issue_id="263", input_path="fixture.docx", news_items=[news]), [candidate]
    )

    assert typed.image_type is ImageType.DECORATIVE
    assert typed.confidence == 0.98
    assert decision.accepted is True
    assert decision.auto_select is False
    assert candidate.selected is False
    assert candidate.curation_status.value == "unselected"
    assert candidate.signals.get("invalid_reason") is None
    assert candidate in result.candidates


def test_product_card_role_is_goods_and_news_linked_brand_event_can_match_without_game_name():
    news = make_news(
        title="Example Brand 冬季商品目录公开",
        event_type=EventType.GOODS,
        game_names=[],
    )
    candidate = make_candidate(
        news,
        url="https://official.example/shop/card-1/main.jpg",
        page="https://official.example/shop/catalog",
        role="goods",
        item_id="card-1",
        container="product card",
        relationship="product card image",
        text="冬季商品目录 goods sample",
        signals={
            "root_source_url": news.source_urls[0],
            "navigation_kind": "goods",
            "navigation_text": "冬季商品目录",
            "page_title": "Example Brand Official Catalog",
        },
    )

    typed = ImageTypeClassifier(load_config().image_types).classify(news, candidate)
    match = EntityMatcher().match(news, candidate)

    assert typed.image_type is ImageType.GOODS
    assert typed.confidence == 0.96
    assert match.matched is True
    assert "official_news_linked_goods" in match.supporting_signals


def test_explicit_other_work_conflict_still_rejects_linked_product_card():
    news = make_news(title="Example Brand 限定商品", event_type=EventType.GOODS, game_names=[])
    candidate = make_candidate(
        news,
        url="https://official.example/shop/card-1/main.jpg",
        page="https://official.example/shop/catalog",
        role="goods",
        item_id="card-1",
        container="product card",
        relationship="product card image",
        signals={"root_source_url": news.source_urls[0], "navigation_kind": "goods", "other_game_name": "Other Game"},
    )

    result = EntityMatcher().match(news, candidate)

    assert result.matched is False
    assert "Other Game" in result.conflicting_entities


def test_sample_word_alone_is_not_a_gameplay_screenshot_but_explicit_screenshot_is():
    news = make_news()
    sample = make_candidate(news, url="https://official.example/news/update/sample01.jpg", page="https://official.example/news/update/gallery")
    screenshot = make_candidate(news, url="https://official.example/news/update/image02.jpg", page="https://official.example/news/update/gallery")
    screenshot.image_alt = "Gameplay screenshot"
    classifier = ImageTypeClassifier(load_config().image_types)

    assert classifier.classify(news, sample).image_type is not ImageType.GAMEPLAY_SCREENSHOT
    assert classifier.classify(news, screenshot).image_type is ImageType.GAMEPLAY_SCREENSHOT


def test_image_local_cg_role_beats_an_auxiliary_arrow_filename():
    news = make_news()
    candidate = make_candidate(news,url="https://official.example/arrow_scene.png",role="game_cg",container="section#gallery",
                               signals={"gallery_path":True,"root_source_url":news.source_urls[0]})
    assert ImageTypeClassifier().classify(news,candidate).image_type is ImageType.GAME_CG


def test_goods_filename_alone_cannot_automatically_select_official_material():
    news=make_news(title="Example Game 商品发售",event_type=EventType.GOODS)
    candidate=make_candidate(news,url="https://official.example/shop/goods_sample.jpg")
    typed=ImageTypeClassifier().classify(news,candidate)
    assert typed.image_type is ImageType.GOODS and typed.confidence == 0.68
    assert ImageRequirementPolicy().evaluate(news,typed).auto_select is False


def test_explicit_banner_alt_overrides_a_broad_legacy_gallery_role():
    news=make_news()
    candidate=make_candidate(news,role="game_cg",container="section#gallery",signals={"gallery_path":True,"root_source_url":news.source_urls[0]})
    candidate.image_alt="Example Game Banner"
    assert ImageTypeClassifier().classify(news,candidate).image_type is ImageType.BANNER


def test_allocator_keeps_cg_ahead_of_background_art_and_x_photo_first():
    news = make_news()
    cg = make_candidate(news, url="https://official.example/cg.jpg")
    cg.image_type = ImageType.GAME_CG
    cg.signals["type_match"] = 1.0
    bg = make_candidate(news, url="https://official.example/bg.jpg")
    bg.image_type = ImageType.BACKGROUND_ART
    x_photo = make_candidate(news, url="https://official.example/x.jpg")
    x_photo.image_type = ImageType.GAMEPLAY_SCREENSHOT
    x_photo.signals["x_api_photo"] = True
    for candidate, score in ((cg, 80), (bg, 99), (x_photo, 1)):
        candidate.score = None
        candidate.signals["test_score"] = score
        # Minimal score objects are easiest to create through model validation.
        from galgame_news.domain import ScoreBreakdown
        candidate.score = ScoreBreakdown(relevance=0.25, freshness=0.25, source_trust=0.25, quality=0.25, total=score)

    ImageAllocator(min_images=0, max_images=1, per_news_max=20, type_limits={"game_cg": 20, "background_art": 20, "gameplay_screenshot": 20}).allocate([news], [bg, cg, x_photo])
    assert x_photo.selected is True

    ImageAllocator(min_images=0, max_images=1, per_news_max=20, type_limits={"game_cg": 20, "background_art": 20}).allocate([news], [bg, cg])
    assert cg.selected is True
    assert bg.selected is False
