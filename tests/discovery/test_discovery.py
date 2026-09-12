from datetime import datetime, timezone

import pytest

from galgame_news.domain import (
    CollectionContext,
    DiscoveryMethod,
    ImageNeed,
    NewsItem,
    ReviewReason,
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


def test_gallery_anchor_prefers_full_image_over_thumbnail_and_picture_source():
    from galgame_news.discovery.adapters import OfficialHtmlAdapter

    html = """
    <picture><source srcset="/hero.webp" type="image/webp"><img src="/hero-thumb.jpg"></picture>
    <a href="/gallery06.jpg" data-lightbox="simple-group"><img src="/gallery06s.jpg"></a>
    """
    adapter = OfficialHtmlAdapter(transport=lambda url, **_: FakeResponse(text=html, url=url))
    result = adapter.collect(item(), SourceRef(url="https://official.example/gallery", domain="official.example", source_type=SourceType.OFFICIAL_SITE), CollectionContext())
    urls = [candidate.image_url for candidate in result.candidates]
    assert "https://official.example/gallery06.jpg" in urls
    assert "https://official.example/gallery06s.jpg" not in urls
    assert "https://official.example/hero.webp" in urls


def test_gallery_page_collects_more_than_five_original_images():
    from galgame_news.discovery.adapters import OfficialHtmlAdapter

    html = "".join(
        f'<a href="/gallery/cg{index:02d}.jpg"><img src="/gallery/thumb/cg{index:02d}s.jpg"></a>'
        for index in range(1, 7)
    )
    adapter = OfficialHtmlAdapter(transport=lambda url, **_: FakeResponse(text=html, url=url))
    result = adapter.collect(
        item(),
        SourceRef(url="https://official.example/gallery", domain="official.example", source_type=SourceType.OFFICIAL_SITE),
        CollectionContext(max_candidates=20),
    )
    assert [candidate.image_url for candidate in result.candidates] == [
        f"https://official.example/gallery/cg{index:02d}.jpg" for index in range(1, 7)
    ]


def test_wix_transformed_thumbnail_is_upgraded_to_original_media_url():
    from galgame_news.discovery.adapters import OfficialHtmlAdapter

    transformed = "https://static.wixstatic.com/media/abc123~mv2.png/v1/fill/w_485,h_273,q_90/abc123~mv2.png"
    html = f"<img src=\"{transformed}\">"
    adapter = OfficialHtmlAdapter(transport=lambda url, **_: FakeResponse(text=html, url=url))
    result = adapter.collect(item(), SourceRef(url="https://reterial.wixsite.com/gallery", domain="reterial.wixsite.com", source_type=SourceType.OFFICIAL_SITE), CollectionContext())
    assert result.candidates[0].image_url == "https://static.wixstatic.com/media/abc123~mv2.png"


def test_direct_image_adapter_returns_candidate_without_fetching_html():
    from galgame_news.discovery.adapters import DirectImageAdapter

    result = DirectImageAdapter().collect(item(), SourceRef(url="https://cdn.example/cg.jpg", domain="cdn.example", source_type=SourceType.DIRECT_IMAGE), CollectionContext())
    assert [c.image_url for c in result.candidates] == ["https://cdn.example/cg.jpg"]


def test_x_without_token_keeps_source_and_requires_manual_review():
    from galgame_news.discovery.adapters import XAdapter

    source = SourceRef(url="https://x.com/AirVelo2026/status/1", domain="x.com", source_type=SourceType.OFFICIAL_X)
    result = XAdapter(token=None, public_transport=lambda url, **_: FakeResponse(text="", url=url)).collect(item(), source, CollectionContext())
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


def test_default_resolver_discovers_same_domain_gallery_using_configured_depth():
    from galgame_news.discovery.resolver import DefaultSourceResolver

    pages = {
        "https://official.example/product": '<a href="/gallery">Gallery</a><a href="https://other.example/gallery">other</a>',
        "https://official.example/gallery": '<a href="/special/cg">CG Special</a>',
    }
    resolver = DefaultSourceResolver(same_domain_depth=2, same_domain_transport=lambda url, **_: FakeResponse(text=pages[url], url=url), search_provider=lambda _news: [])
    result = resolver.resolve(item(source_urls=["https://official.example/product"]))
    assert [source.url for source in result] == ["https://official.example/product", "https://official.example/gallery", "https://official.example/special/cg"]


def test_document_link_trust_does_not_mark_known_third_party_as_official():
    from galgame_news.discovery.resolver import DefaultSourceResolver

    resolver = DefaultSourceResolver(search_provider=lambda _news: [])
    result = resolver.resolve(item(source_urls=["https://vndb.org/v123", "https://store.steampowered.com/app/1"]))
    assert result[0].source_type is SourceType.UNVERIFIED
    assert result[0].officiality < 0.5
    assert result[1].source_type is SourceType.STEAM
    assert result[1].officiality < 1.0


def test_ddgs_provider_accepts_current_href_and_body_field_names():
    from galgame_news.discovery.search import DDGSSearchProvider

    provider = DDGSSearchProvider(
        search_fn=lambda query, **_: [
            {"title": "Official game site", "href": "https://official.example/game", "body": "Gallery and news"}
        ]
    )
    result = provider.search(item())
    assert [source.url for source in result] == ["https://official.example/game"]
    assert result[0].source_type is SourceType.UNVERIFIED


def test_wix_embedded_dynamic_data_yields_original_media_images():
    from galgame_news.discovery.adapters import OfficialHtmlAdapter

    html = r'''<script id="wix-warmup-data" type="application/json">{"gallery":{"items":[{"src":"https:\/\/static.wixstatic.com\/media\/one~mv2.jpg\/v1\/fill\/w_400,h_200\/one.jpg"},{"src":"wix:image://v1/two~mv2.png/two.png#originWidth=1600&originHeight=900"}]}}</script>'''
    adapter = OfficialHtmlAdapter(transport=lambda url, **_: FakeResponse(text=html, url=url))
    result = adapter.collect(item(), SourceRef(url="https://site.wixsite.com/game", domain="site.wixsite.com", source_type=SourceType.OFFICIAL_SITE), CollectionContext())
    assert {candidate.image_url for candidate in result.candidates} == {"https://static.wixstatic.com/media/one~mv2.jpg", "https://static.wixstatic.com/media/two~mv2.png"}


def test_x_without_token_extracts_public_meta_image_and_still_requires_review():
    from galgame_news.discovery.adapters import XAdapter

    html = '<meta property="og:image" content="https://pbs.twimg.com/media/cg.jpg">'
    source = SourceRef(url="https://x.com/studio/status/1", domain="x.com", source_type=SourceType.OFFICIAL_X)
    adapter = XAdapter(token=None, public_transport=lambda url, **_: FakeResponse(text=html, url=url))
    result = adapter.collect(item(), source, CollectionContext())
    assert [candidate.image_url for candidate in result.candidates] == ["https://pbs.twimg.com/media/cg.jpg"]
    assert ReviewReason.X_SOURCE in result.manual_review_reasons
