from datetime import datetime, timezone

from galgame_news.config import load_config
from galgame_news.curation.allocator import ImageAllocator
from galgame_news.domain import ImageCandidate, ImageType, ImageNeed, NewsItem, SourceType


NOW = datetime.now(timezone.utc)


def item():
    return NewsItem(
        issue_id="259", sequence=1, section="新作", title="《Gallery Game》更新", body="",
        game_names=["Gallery Game"], image_need=ImageNeed.EXPLICIT_NEW_IMAGE,
    )


def candidate(index, image_type, *, score=80.0):
    value = ImageCandidate(
        news_id=item().id,
        image_url=f"https://official.example/gallery/{image_type.value}-{index}.jpg",
        source_url="https://official.example/gallery",
        source_type=SourceType.OFFICIAL_SITE,
        image_type=image_type,
        fetched_at=NOW,
        signals={"game_match": 1.0, "auto_select": True},
    )
    value.score = {
        "relevance": 1.0, "freshness": 1.0, "source_trust": 1.0,
        "quality": 1.0, "total": score,
    }
    return value


def test_type_limits_cap_cover_key_visual_character_and_goods_and_keep_candidates():
    values = (
        [candidate(i, ImageType.KEY_VISUAL) for i in range(3)]
        + [candidate(i, ImageType.COVER) for i in range(3)]
        + [candidate(i, ImageType.CHARACTER_ART) for i in range(7)]
        + [candidate(i, ImageType.GOODS) for i in range(12)]
    )
    result = ImageAllocator(
        min_images=0, max_images=None, per_news_max=50,
        type_limits={"key_visual": 1, "cover": 1, "character_art": 4, "goods": 10},
    ).allocate([item()], values)
    selected = [value for value in result if value.selected]
    assert sum(value.image_type is ImageType.KEY_VISUAL for value in selected) == 1
    assert sum(value.image_type is ImageType.COVER for value in selected) == 1
    assert sum(value.image_type is ImageType.CHARACTER_ART for value in selected) == 4
    assert sum(value.image_type is ImageType.GOODS for value in selected) == 10
    assert len(result) == len(values)


def test_unknown_default_limit_is_zero_but_explicit_override_can_select_one():
    values = [candidate(i, ImageType.UNKNOWN) for i in range(2)]
    none = ImageAllocator(min_images=0, max_images=None, per_news_max=20, type_limits={"unknown": 0}).allocate([item()], values)
    assert not any(value.selected for value in none)
    one = ImageAllocator(min_images=0, max_images=None, per_news_max=20, max_unknown_per_news=1, type_limits={"unknown": 0}).allocate([item()], values)
    assert sum(value.selected for value in one) == 1


def test_old_config_without_type_limits_loads_defaults():
    config = load_config()
    assert config.selection.type_limits["game_cg"] == 20
    assert config.selection.type_limits["unknown"] == 0


def test_placeholder_asset_is_rejected_before_transport(tmp_path):
    from galgame_news.curation.download import ImageDownloader

    calls = []
    def transport(url, **_):
        calls.append(url)
        raise AssertionError("placeholder should not be requested")

    value = candidate(1, ImageType.UNKNOWN)
    value.image_url = "https://official.example/assets/now_printing.jpeg"
    accepted, failures = ImageDownloader(load_config(), transport=transport).download([value], tmp_path)
    assert accepted == []
    assert calls == []
    assert failures[0].code == "placeholder_image"
