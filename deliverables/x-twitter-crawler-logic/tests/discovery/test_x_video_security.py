from __future__ import annotations

from galgame_news.domain import CollectionContext, ImageNeed, NewsItem, SourceRef, SourceType


class FakeResponse:
    def __init__(self, *, text: str = "", url: str, status_code: int = 200):
        self.status_code = status_code
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = {"content-type": "text/html"}
        self.url = url


def _item() -> NewsItem:
    return NewsItem(
        issue_id="259",
        sequence=1,
        section="新作",
        title="Video Game",
        body="video",
        game_names=["Video Game"],
        image_need=ImageNeed.EXPLICIT_NEW_IMAGE,
    )


def _source() -> SourceRef:
    return SourceRef(
        url="https://x.com/studio/status/1",
        domain="x.com",
        source_type=SourceType.OFFICIAL_X,
    )


def _fixed_public_resolver(_host: str) -> list[str]:
    return ["93.184.216.34"]


def test_public_x_only_accepts_explicit_post_body_links_not_navigation_links():
    from galgame_news.discovery.adapters import XAdapter

    html = """
    <a href="https://www.youtube.com/watch?v=navigation">navigation</a>
    <a href="https://x.com/studio/status/999">internal status</a>
    <a href="https://x.com/intent/post?text=share">share</a>
    <a href="https://x.com/home">home</a>
    <div data-testid="tweetText">
      <a href="https://www.youtube.com/watch?v=body">post video</a>
    </div>
    <div data-testid="card.wrapper">
      <a href="https://official.example/pv">official card</a>
    </div>
    """

    def transport(url: str, **_kwargs):
        if url == "https://x.com/studio/status/1":
            return FakeResponse(text=html, url=url)
        if url == "https://official.example/pv":
            return FakeResponse(text='<video src="/movie/pv.mp4">', url=url)
        raise AssertionError(f"unexpected request: {url}")

    result = XAdapter(public_transport=transport, public_resolver=_fixed_public_resolver).collect(
        _item(), _source(), CollectionContext()
    )

    urls = {candidate.video_url for candidate in result.video_candidates}
    assert "https://www.youtube.com/watch?v=body" in urls
    assert "https://official.example/movie/pv.mp4" in urls
    assert "https://www.youtube.com/watch?v=navigation" not in urls
    assert not any("x.com" in url and "/status/999" in url for url in urls)


def test_x_entity_urls_use_public_url_validation_and_isolate_ssrf_failures():
    from galgame_news.discovery.adapters import XAdapter

    payload = {
        "data": {
            "entities": {
                "urls": [
                    {"expanded_url": "https://www.youtube.com/watch?v=valid"},
                    {"expanded_url": "http://127.0.0.1/video.mp4"},
                    {"expanded_url": "http://[::1]/video.mp4"},
                    {"expanded_url": "http://10.0.0.1/video.mp4"},
                    {"expanded_url": "https://official.example/private"},
                ]
            }
        }
    }

    def api_transport(url: str, **_kwargs):
        assert url == "https://x.com/studio/status/1"
        return payload

    def public_transport(url: str, **_kwargs):
        assert url == "https://official.example/private"
        return FakeResponse(
            text='<video src="https://127.0.0.1/child.mp4">',
            url="http://127.0.0.1/redirected",
        )

    result = XAdapter(
        token="test-token",
        transport=api_transport,
        public_transport=public_transport,
        public_resolver=_fixed_public_resolver,
    ).collect(_item(), _source(), CollectionContext())

    assert {candidate.video_url for candidate in result.video_candidates} == {
        "https://www.youtube.com/watch?v=valid"
    }
    assert len(result.failures) == 4
    assert all(failure.code == "unsafe_external_url" for failure in result.failures)
    assert all(failure.retryable is False for failure in result.failures)


def test_safe_http_client_rejects_public_redirect_to_private_address():
    from galgame_news.discovery.http import SafeHttpClient, UnsafeUrlError

    def transport(url: str, **_kwargs):
        return FakeResponse(text="redirected", url="http://192.168.1.20/private")

    client = SafeHttpClient(transport=transport, resolver=_fixed_public_resolver)
    try:
        client.get("https://public.example/page")
    except UnsafeUrlError:
        pass
    else:
        raise AssertionError("private redirect was not rejected")
