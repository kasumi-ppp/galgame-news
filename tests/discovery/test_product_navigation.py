from pathlib import Path
from types import SimpleNamespace

from bs4 import BeautifulSoup

from galgame_news.discovery.product_navigation import product_links
from galgame_news.discovery.resolver import DefaultSourceResolver
from galgame_news.domain import EventType, NewsItem, SourceRef, SourceType


def news(url="https://publisher.example/event/", **changes):
    values = dict(issue_id="263", sequence=1, section="周边", title="Studio秋季通贩",
                  body="《Work》商品", event_type=EventType.GOODS, game_names=["Work"], source_urls=[url])
    values.update(changes)
    return NewsItem(**values)


def source(url="https://publisher.example/event/", **changes):
    return SourceRef(url=url, root_url=url, domain="publisher.example", source_type=SourceType.OFFICIAL_SITE,
                     officiality=.8, **changes)


def test_cards_named_first_and_recommendations_excluded():
    page = '''<title>Studio 秋季</title><article><div class="itemListBox"><a href="/item/other">Other</a><p>商品 1000円</p></div></article>
    <article><div class="itemListBox"><a href="/item/work">Work</a><p>商品 1000円</p></div></article>
    <div class="product-card"><a href="/recommend/item">Work</a></div><a href="/cart">Work</a>'''
    found = product_links(news(), source(), source().url, BeautifulSoup(page, "html.parser"))
    assert [x[0] for x in found] == ["https://publisher.example/item/work", "https://publisher.example/item/other"]


def test_cross_domain_only_explicit_official_shop():
    soup = BeautifulSoup('<a href="https://shop.example/">公式通販</a><a href="https://ads.example/">Workグッズ</a>', "html.parser")
    found = product_links(news(), source(), source().url, soup)
    assert len(found) == 1 and found[0][2] == "shop"


def test_anchor_product_cards_and_recommendation_parent():
    url="https://publisher.example/products/list?filter=work"
    soup=BeautifulSoup('<a class="product-card" href="/products/detail/main">Work商品</a><aside class="related products"><a class="product-card" href="/products/detail/other">Work商品</a></aside>',"html.parser")
    found=product_links(news(url=url),source(url),url,soup)
    assert [row[0] for row in found] == ["https://publisher.example/products/detail/main"]


def test_schema_graph_products_and_no_goods_news_navigation():
    soup = BeautifulSoup('<script type="application/ld+json">{"@graph":[{"@type":"Product","name":"Work時計","url":"/item/clock"}]}</script>', "html.parser")
    assert product_links(news(), source(), source().url, soup)[0][0].endswith("/item/clock")
    assert product_links(news(event_type=EventType.UNKNOWN, title="Work CG更新", body="CG"), source(), source().url, soup) == []


def test_entry_current_event_and_age_entry():
    resolver = DefaultSourceResolver(search_provider=lambda _: [])
    n = news(url="https://publisher.example/", title="CUFFSの秋通販2026开启", game_names=[])
    s = source("https://publisher.example/")
    soup = BeautifulSoup('<div>あなたは18歳以上ですか</div><li id="yes"><a href="/main/">YES</a></li>', "html.parser")
    children = list(resolver._page_children(s, n, s.url, s.url, soup, True))
    assert children[0].navigation_kind == "age_confirmation"
    soup = BeautifulSoup('<a href="/store/autumn"><img alt="CUFFSの秋通販2026"></a><a href="/store/summer">CUFFS夏のグッズ通販2026</a>', "html.parser")
    assert [x.url for x in resolver._page_children(s, n, s.url, s.url, soup, True)] == ["https://publisher.example/store/autumn"]


def test_shared_budget_keeps_16_pages_and_three_levels():
    n = news(game_names=["Work"])
    calls = []
    def fetch(url, **_):
        calls.append(url)
        return SimpleNamespace(text='<title>Work</title>' + ''.join(f'<div class="product-card"><a href="/item/{i}">Work 商品{i}</a></div>' for i in range(30)), url=url)
    resolver = DefaultSourceResolver(same_domain_transport=fetch, search_provider=lambda _: [])
    # Inject transport's DNS without making public requests.
    resolver.same_domain_client.resolver = lambda _: ["8.8.8.8"]
    refs = resolver.resolve(n)
    assert len(refs) == 16
    assert len(set(calls)) <= 16
