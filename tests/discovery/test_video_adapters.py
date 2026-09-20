from __future__ import annotations

from datetime import datetime, timezone

from galgame_news.domain import CollectionContext, ImageNeed, NewsItem, SourceRef, SourceType


class FakeResponse:
    def __init__(self, *, text="", url="https://official.example/page"):
        self.status_code = 200
        self.text = text
        self.content = text.encode()
        self.headers = {"content-type": "text/html"}
        self.url = url


def item(**kwargs):
    data = dict(
        issue_id="259",
        sequence=1,
        section="新作",
        title="Video Game",
        body="video",
        game_names=["Video Game"],
        image_need=ImageNeed.EXPLICIT_NEW_IMAGE,
    )
    data.update(kwargs)
    return NewsItem(**data)


def test_official_html_collects_video_source_tags_iframes_and_direct_links():
    from galgame_news.discovery.adapters import OfficialHtmlAdapter

    html = """
    <video src="/movie/op.mp4"><source src="/movie/op.webm"></video>
    <source src="https://cdn.example/clip.m3u8">
    <iframe src="https://www.youtube-nocookie.com/embed/abc123"></iframe>
    <iframe src="https://www.youtube.com/watch?v=abc123"></iframe>
    <a href="/movie/op.mp4">duplicate</a>
    """
    result = OfficialHtmlAdapter(
        transport=lambda url, **_: FakeResponse(text=html, url=url)
    ).collect(
        item(),
        SourceRef(
            url="https://official.example/page",
            domain="official.example",
            source_type=SourceType.OFFICIAL_SITE,
        ),
        CollectionContext(),
    )

    urls = [candidate.video_url for candidate in result.video_candidates]
    assert urls == [
        "https://official.example/movie/op.mp4",
        "https://official.example/movie/op.webm",
        "https://cdn.example/clip.m3u8",
        "https://www.youtube.com/watch?v=abc123",
    ]
    assert all(candidate.source_url == "https://official.example/page" for candidate in result.video_candidates)


def test_video_document_source_and_youtube_urls_are_canonicalized():
    from galgame_news.discovery.resolver import DefaultSourceResolver

    resolver = DefaultSourceResolver(search_provider=lambda _news: [])
    sources = resolver.resolve(
        item(
            source_urls=[
                "https://youtu.be/abc123?t=2",
                "https://www.youtube.com/embed/abc123",
                "https://cdn.example/trailer.mp4",
            ]
        )
    )
    assert [source.source_type for source in sources] == [
        SourceType.VIDEO,
        SourceType.VIDEO,
        SourceType.VIDEO,
    ]


def test_x_api_entities_urls_youtube_and_official_page_are_collected_with_one_hop():
    from galgame_news.discovery.adapters import XAdapter

    official_html = '<video src="/pv/game.mp4"></video>'
    payload = {
        "data": {
            "entities": {
                "urls": [
                    {"expanded_url": "https://youtu.be/abc123"},
                    {"expanded_url": "https://official.example/pv"},
                ]
            }
        }
    }

    def transport(url, **kwargs):
        if url == "https://x.com/studio/status/1":
            return payload
        return FakeResponse(text=official_html, url=url)

    source = SourceRef(
        url="https://x.com/studio/status/1",
        domain="x.com",
        source_type=SourceType.OFFICIAL_X,
    )
    result = XAdapter(token="test-token", transport=transport, public_resolver=lambda _host: ["93.184.216.34"]).collect(
        item(), source, CollectionContext()
    )
    assert {candidate.video_url for candidate in result.video_candidates} == {
        "https://www.youtube.com/watch?v=abc123",
        "https://official.example/pv/game.mp4",
    }


def test_x_external_search_like_unrelated_url_is_not_added_without_entities():
    from galgame_news.discovery.adapters import XAdapter

    source = SourceRef(
        url="https://x.com/studio/status/1",
        domain="x.com",
        source_type=SourceType.OFFICIAL_X,
    )
    result = XAdapter(
        token="test-token",
        transport=lambda url, **kwargs: {"data": {"text": "https://random.example/video.mp4"}},
    ).collect(item(), source, CollectionContext())
    assert result.video_candidates == []
