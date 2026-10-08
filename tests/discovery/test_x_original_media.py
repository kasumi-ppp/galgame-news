"""Original X photo URLs retain an auditable API source and dimensions."""

import pytest

from galgame_news.discovery.adapters.common import _is_public_x_media, _upgrade_image_url
from galgame_news.discovery.adapters.x import XAdapter
from galgame_news.domain import CollectionContext, ImageNeed, NewsItem, SourceRef, SourceType


@pytest.mark.parametrize("source,expected", [
    ("https://pbs.twimg.com/media/photo.jpg", "https://pbs.twimg.com/media/photo.jpg?name=orig"),
    ("https://pbs.twimg.com/media/photo?format=png", "https://pbs.twimg.com/media/photo?format=png&name=orig"),
    ("https://pbs.twimg.com/media/photo?format=jpg&name=small", "https://pbs.twimg.com/media/photo?format=jpg&name=orig"),
    ("https://pbs.twimg.com/media/photo.jpg:small", "https://pbs.twimg.com/media/photo.jpg?name=orig"),
    ("https://pbs.twimg.com/media/photo.jpg:large", "https://pbs.twimg.com/media/photo.jpg?name=orig"),
    ("https://pbs.twimg.com/media/photo.jpg:orig", "https://pbs.twimg.com/media/photo.jpg?name=orig"),
    ("https://pbs.twimg.com/media/photo.jpg:large?format=jpg", "https://pbs.twimg.com/media/photo.jpg?format=jpg&name=orig"),
    ("https://pbs.twimg.com/media/photo?format=webp&Name=small&name=large&width=20", "https://pbs.twimg.com/media/photo?format=webp&name=orig"),
])
def test_x_photo_original_url_normalization(source, expected):
    assert _upgrade_image_url(source) == expected
    assert _upgrade_image_url(expected) == expected


@pytest.mark.parametrize("url", [
    "https://pbs.twimg.com/profile_images/avatar.jpg?name=small",
    "https://pbs.twimg.com/card_img/card.jpg?name=small",
    "https://pbs.twimg.com/ext_tw_video_thumb/preview.jpg?name=small",
    "http://pbs.twimg.com/media/photo.jpg?name=small",
    "https://user:pass@pbs.twimg.com/media/photo.jpg?name=small",
    "https://pbs.twimg.com:8443/media/photo.jpg?name=small",
    "https://pbs.twimg.com.evil.example/media/photo.jpg?name=small",
])
def test_x_original_upgrade_leaves_other_paths_and_unsafe_origins_untouched(url):
    assert _upgrade_image_url(url) == url


@pytest.mark.parametrize("url", [
    "http://pbs.twimg.com/media/photo.jpg",
    "https://user:pass@pbs.twimg.com/media/photo.jpg",
    "https://pbs.twimg.com:8443/media/photo.jpg",
    "https://pbs.twimg.com:invalid/media/photo.jpg",
    "https://pbs.twimg.com.evil.example/media/photo.jpg",
    "https://pbs.twimg.com/media/",
    "https://[invalid]/media/photo.jpg",
])
def test_x_rejects_unsafe_media_origins(url):
    assert _is_public_x_media(url) is False
    assert XAdapter._api_image_urls({"media": [{"type": "photo", "media_url_https": url}]}) == []


def test_x_api_collection_preserves_raw_links_and_matching_original_info():
    raw_first = "https://pbs.twimg.com/media/first?format=jpg&name=small"
    raw_second = "https://pbs.twimg.com/media/second.png:large"
    payload = {"data": {"text": "Game update", "attachments": {"media_keys": ["first", "second"]}}, "includes": {"media": [
        {"type": "photo", "media_key": "unrelated", "url": "https://pbs.twimg.com/media/other.jpg", "original_info": {"width": 9, "height": 9}},
        {"type": "photo", "media_key": "first", "url": raw_first, "original_info": {"width": 1920, "height": 1080}},
        {"type": "photo", "media_key": "second", "url": raw_second, "original_info": {"width": 1200, "height": 1600}},
    ]}}
    post_url = "https://x.com/studio/status/123"
    news = NewsItem(issue_id="261", sequence=1, section="周报", title="Game", body="update", image_need=ImageNeed.EXPLICIT_NEW_IMAGE)
    source = SourceRef(url=post_url, domain="x.com", source_type=SourceType.OFFICIAL_X)
    adapter = XAdapter(token="test", socialdata_mode=True, transport=lambda *_a, **_kw: payload)
    result = adapter.collect(news, source, CollectionContext())
    assert len(result.candidates) == 2
    first, second = result.candidates
    assert first.image_url == "https://pbs.twimg.com/media/first?format=jpg&name=orig"
    assert first.media_source_url == raw_first
    assert (first.expected_width, first.expected_height) == (1920, 1080)
    assert second.image_url == "https://pbs.twimg.com/media/second.png?name=orig"
    assert second.media_source_url == raw_second
    assert (second.expected_width, second.expected_height) == (1200, 1600)
    assert first.id == first.model_copy().id


def test_x_metadata_ignores_invalid_dimensions():
    url = "https://pbs.twimg.com/media/image.jpg"
    payload = {"media": [{"type": "photo", "media_url_https": url, "original_info": {"width": -1, "height": True}}]}
    assert XAdapter._api_image_metadata(payload, _upgrade_image_url(url)) == (url, None, None)


def test_x_metadata_ignores_unrelated_malformed_url_text():
    url = "https://pbs.twimg.com/media/image.jpg"
    payload = {"text": "https://[invalid]", "media": [{"type": "photo", "media_url_https": url}]}
    assert XAdapter._api_image_metadata(payload, _upgrade_image_url(url)) == (url, None, None)
