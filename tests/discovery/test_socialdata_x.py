from __future__ import annotations

from galgame_news.discovery.adapters.x import XAdapter
from galgame_news.discovery.x_api import SocialDataTweetTransport
from galgame_news.domain import CollectionContext, ImageNeed, NewsItem, SourceRef, SourceType


def _news() -> NewsItem:
    post_url = "https://x.com/studio/status/2100750400404041890"
    return NewsItem(
        issue_id="261", sequence=1, section="周报", title="Test Game",
        body="update", game_names=["Test Game"], image_need=ImageNeed.EXPLICIT_NEW_IMAGE,
        source_urls=[post_url],
    )


def _source() -> SourceRef:
    return SourceRef(
        url="https://x.com/studio/status/2100750400404041890",
        domain="x.com", source_type=SourceType.OFFICIAL_X,
    )


def test_socialdata_transport_looks_up_one_status_with_bearer_header_and_caches_it():
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"id_str": "2100750400404041890", "extended_entities": {"media": []}}

    def get(url, *, headers, timeout):
        calls.append((url, headers, timeout))
        return Response()

    transport = SocialDataTweetTransport(http_get=get, response_cache={})
    first = transport(_source().url, token="secret", timeout=5)
    second = transport(_source().url, token="secret", timeout=5)

    assert first == second
    assert len(calls) == 1
    assert calls[0][0] == "https://api.socialdata.tools/twitter/tweets/2100750400404041890"
    assert calls[0][1]["Authorization"] == "Bearer secret"
    assert "secret" not in calls[0][0]


def test_socialdata_post_photos_are_marked_for_priority_and_video_variant_is_extracted():
    payload = {
        "id_str": "2100750400404041890",
        "full_text": "Test Game update",
        "extended_entities": {"media": [
            {"type": "photo", "media_url_https": "https://pbs.twimg.com/media/photo-1.jpg?name=small", "alt_text": "Test Game event CG"},
            {
                "type": "video",
                "media_url_https": "https://pbs.twimg.com/media/video-preview.jpg",
                "video_info": {"variants": [
                    {"content_type": "application/x-mpegURL", "url": "https://video.twimg.com/playlist.m3u8"},
                    {"content_type": "video/mp4", "bitrate": 832000, "url": "https://video.twimg.com/ext_tw_video/1/pu/vid/avc1/clip.mp4"},
                ]},
            },
            {
                "type": "video",
                "video_info": {"variants": [
                    {"content_type": "application/x-mpegURL", "url": "https://video.twimg.com/ext_tw_video/2/pu/pl/playlist.m3u8"},
                ]},
            },
        ]},
    }
    adapter = XAdapter(
        token="secret", transport=lambda *_args, **_kwargs: payload,
        socialdata_mode=True, public_resolver=lambda _host: ["93.184.216.34"],
    )

    result = adapter.collect(_news(), _source(), CollectionContext())

    assert [candidate.image_url for candidate in result.candidates] == [
        "https://pbs.twimg.com/media/photo-1.jpg?name=orig"
    ]
    assert result.candidates[0].signals["socialdata_photo"] is True
    assert result.candidates[0].signals["x_api_photo"] is True
    assert result.candidates[0].image_alt == "Test Game event CG"
    assert result.candidates[0].nearby_text == "Test Game update"
    assert [video.video_url for video in result.video_candidates] == [
        "https://video.twimg.com/ext_tw_video/1/pu/vid/avc1/clip.mp4",
        "https://video.twimg.com/ext_tw_video/2/pu/pl/playlist.m3u8",
    ]


def test_socialdata_transport_rejects_non_x_status_urls_before_network():
    calls = []
    transport = SocialDataTweetTransport(http_get=lambda *args, **kwargs: calls.append(args))

    try:
        transport("https://example.com/status/123", token="secret", timeout=5)
    except ValueError as exc:
        assert "X status URL" in str(exc)
    else:
        raise AssertionError("non-X status URL was accepted")
    assert calls == []
