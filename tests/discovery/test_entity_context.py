from galgame_news.domain import CollectionContext, SourceRef, SourceType
from galgame_news.discovery.adapters import OfficialHtmlAdapter
from tests.discovery.test_discovery import FakeResponse, item


def test_official_html_attaches_page_title_and_alt_context_to_candidates():
    html = """
    <html><head><title>Other Game Official Gallery</title></head>
    <body><img src="/gallery/cg01.jpg" alt="Other Game CG"></body></html>
    """
    adapter = OfficialHtmlAdapter(transport=lambda url, **_: FakeResponse(text=html, url=url))
    result = adapter.collect(item(), SourceRef(url="https://official.example/gallery", domain="official.example", source_type=SourceType.OFFICIAL_SITE), CollectionContext())
    assert result.candidates
    assert result.candidates[0].signals["page_title"] == "Other Game Official Gallery"
    assert result.candidates[0].signals["alt"] == "Other Game CG"
