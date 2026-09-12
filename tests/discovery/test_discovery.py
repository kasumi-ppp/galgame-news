from datetime import datetime, timezone

import pytest

from galgame_news.domain import (
    CollectionContext,
    DiscoveryMethod,
    ImageNeed,
    NewsItem,
    SourceRef,
    SourceType,
)


def item(**kwargs):
    data = dict(
        issue_id="259",
        sequence=1,
        section="新作",
        title="《Happy Weekend》主机板制作完成",
        body="HOOKSOFT 公布一张贺图",
        game_names=["Happy Weekend"],
        organizations=["HOOKSOFT"],
        image_need=ImageNeed.EXPLICIT_NEW_IMAGE,
    )
    data.update(kwargs)
    return NewsItem(**data)


class FakeResponse:
    def __init__(self, status_code=200, text="", content=b"", headers=None, url=None):
        self.status_code = status_code
        self.text = text
        self.content = content
        self.headers = headers or {"content-type": "text/html"}
        self.url = url or "https://official.example/news"


def test_official_html_extracts_meta_lazy_srcset_links_and_jsonld():
    from galgame_news.discovery.adapters import OfficialHtmlAdapter

    html = """
    <html><head>
      <meta property="og:image" content="/og.jpg">
      <meta name="twitter:image" content="https://official.example/tw.jpg">
      <script type="application/ld+json">{"image": ["/jsonld.jpg"]}</script>
    </head><body>
      <img src="/normal.jpg" data-src="/lazy.jpg" srcset="/small.jpg 400w, /large.jpg 1200w">
      <a href="/linked.png">image</a>
    </body></html>
    """
    adapter = OfficialHtmlAdapter(transport=lambda url, **_: FakeResponse(text=html, url=url))
    result = adapter.collect(item(), SourceRef(url="https://official.example/news", domain="official.example", source_type=SourceType.OFFICIAL_SITE), CollectionContext())
    urls = {candidate.image_url for candidate in result.candidates}
    assert {"https://official.example/og.jpg", "https://official.example/tw.jpg", "https://official.example/jsonld.jpg", "https://official.example/normal.jpg", "https://official.example/lazy.jpg", "https://official.example/large.jpg", "https://official.example/linked.png"} <= urls


def test_direct_image_adapter_returns_candidate_without_fetching_html():
    from galgame_news.discovery.adapters import DirectImageAdapter

    result = DirectImageAdapter().collect(item(), SourceRef(url="https://cdn.example/cg.jpg", domain="cdn.example", source_type=SourceType.DIRECT_IMAGE), CollectionContext())
    assert [c.image_url for c in result.candidates] == ["https://cdn.example/cg.jpg"]


def test_x_without_token_keeps_source_and_requires_manual_review():
    from galgame_news.discovery.adapters import XAdapter

    source = SourceRef(url="https://x.com/AirVelo2026/status/1", domain="x.com", source_type=SourceType.OFFICIAL_X)
    result = XAdapter(token=None).collect(item(), source, CollectionContext())
    assert not result.candidates
    assert result.manual_review_reasons
    assert source.requires_review is True


def test_http_client_rejects_private_and_rechecks_redirects():
    from galgame_news.discovery.http import SafeHttpClient, UnsafeUrlError

    client = SafeHttpClient(transport=lambda url, **_: FakeResponse(url="http://127.0.0.1/secret"))
    with pytest.raises(UnsafeUrlError):
        client.get("http://127.0.0.1/image.jpg")
    with pytest.raises(UnsafeUrlError):
        client.get("https://public.example/image.jpg")


def test_http_client_retries_429_then_succeeds():
    from galgame_news.discovery.http import SafeHttpClient

    calls = []

    def transport(url, **kwargs):
        calls.append(url)
        return FakeResponse(status_code=429 if len(calls) < 3 else 200, content=b"ok", headers={"content-type": "text/plain"}, url=url)

    response = SafeHttpClient(transport=transport, max_retries=3).get("https://public.example/page")
    assert response.status_code == 200
    assert len(calls) == 3


def test_source_resolver_order_document_history_same_domain_then_search():
    from galgame_news.discovery.resolver import DefaultSourceResolver

    history = [SourceRef(url="https://history.example/game", domain="history.example", source_type=SourceType.OFFICIAL_SITE, discovered_via=DiscoveryMethod.HISTORY, officiality=1.0)]
    search_calls = []

    resolver = DefaultSourceResolver(
        history_lookup=lambda news: history,
        same_domain_lookup=lambda source, news: [SourceRef(url="https://history.example/gallery", domain="history.example", source_type=SourceType.OFFICIAL_SITE, discovered_via=DiscoveryMethod.SAME_DOMAIN, officiality=1.0)],
        search_provider=lambda news: (search_calls.append(news.id) or [SourceRef(url="https://search.example/result", domain="search.example", source_type=SourceType.UNVERIFIED, discovered_via=DiscoveryMethod.DDGS)]),
    )
    result = resolver.resolve(item(source_urls=["https://doc.example/news", "https://doc.example/news#fragment"]))
    assert [source.url for source in result][:3] == ["https://doc.example/news", "https://history.example/game", "https://history.example/gallery"]
    assert result[-1].officiality == 0.0
    assert search_calls


def test_dynamic_and_age_gate_sources_are_reviewable():
    from galgame_news.discovery.adapters import OfficialHtmlAdapter

    html = "<html><head><meta name='age-verification' content='required'></head><body><script>dynamic()</script></body></html>"
    source = SourceRef(url="https://official.example/age", domain="official.example", source_type=SourceType.OFFICIAL_SITE)
    result = OfficialHtmlAdapter(transport=lambda url, **_: FakeResponse(text=html, url=url)).collect(item(), source, CollectionContext())
    assert not result.candidates
    assert source.requires_review
