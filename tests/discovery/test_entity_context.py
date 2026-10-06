from galgame_news.domain import CollectionContext, SourceRef, SourceType
from galgame_news.discovery.adapters import OfficialHtmlAdapter
from galgame_news.domain import ImageNeed, NewsItem


def item():
    return NewsItem(
        issue_id="259", sequence=1, section="新作", title="Example Game news",
        body="Official update", game_names=["Example Game"],
        source_urls=["https://official.example/news/item"],
        image_need=ImageNeed.EXPLICIT_NEW_IMAGE,
    )


class FakeResponse:
    def __init__(self, *, text, url):
        self.status_code = 200
        self.text = text
        self.content = text.encode()
        self.headers = {"content-type": "text/html"}
        self.url = url


def test_official_html_attaches_page_title_and_alt_context_to_candidates():
    html = """
    <html><head><title>Other Game Official Gallery</title></head>
    <body><img src="/gallery/cg01.jpg" alt="Other Game CG"></body></html>
    """
    adapter = OfficialHtmlAdapter(transport=lambda url, **_: FakeResponse(text=html, url=url))
    result = adapter.collect(item(), SourceRef(url="https://official.example/gallery", domain="official.example", source_type=SourceType.OFFICIAL_SITE, root_url="https://official.example/news/item", parent_url="https://official.example/news/item"), CollectionContext())
    assert result.candidates
    assert result.candidates[0].signals["page_title"] == "Other Game Official Gallery"
    assert result.candidates[0].signals["alt"] == "Other Game CG"
    assert result.candidates[0].signals["root_source_url"] == "https://official.example/news/item"
    assert result.candidates[0].signals["parent_source_url"] == "https://official.example/news/item"
