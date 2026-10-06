"""Bounded, contextual product links; URL spelling alone never authorizes a shop."""

from __future__ import annotations

import json
import re
import unicodedata
from urllib.parse import urljoin, urlsplit


def _key(text):
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text).casefold())


def goods_news(news):
    return news.event_type.value == "goods" or bool(re.search(
        r"周边|周邊|商品|グッズ|通贩|通販|再販|复刻|復刻|goods|merchandise", news.title + " " + news.body, re.I))


def _json_products(value):
    if isinstance(value, list):
        for child in value:
            yield from _json_products(child)
    elif isinstance(value, dict):
        kinds = value.get("@type", [])
        if "Product" in ([kinds] if isinstance(kinds, str) else kinds):
            yield value
        for key in ("@graph", "itemListElement", "item"):
            if key in value:
                yield from _json_products(value[key])


def product_links(news, source, page_url, soup):
    """Return ordered (URL, local label, kind), restricted to relevant goods context."""
    if not goods_news(news):
        return []
    quoted = re.findall(r"《([^》]+)》", news.body)
    brand = re.match(r"^([A-Za-z][A-Za-z0-9 &.-]+?)(?=开启|宣布|推出|の|\s*20)", news.title)
    names = [_key(str(x)) for x in [*news.game_names, *news.organizations, *news.keywords, *quoted, *([brand[1]] if brand else [])]]
    names = [x for x in names if len(x) >= 3]
    page_host = (urlsplit(page_url).hostname or "").casefold()
    root = source.root_url or source.url
    explicit_root = any(urlsplit(x)._replace(fragment="") == urlsplit(root)._replace(fragment="") for x in news.source_urls)
    page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
    bound_page = explicit_root and (source.navigation_kind in {"goods", "shop", "event"} or
        any(x in _key(page_title) for x in names) or bool(re.search(r"/(?:item|goods|shop|store|event|products?)(?:/|[._-])", urlsplit(page_url).path, re.I)))
    found = []
    for order, anchor in enumerate(soup.find_all("a", href=True)):
        href = str(anchor["href"])
        url = urljoin(page_url, href)
        p = urlsplit(url)
        if p.scheme not in {"http", "https"} or p.username or p.password:
            continue
        if re.search(r"(?:cart|checkout|login|account|wishlist|recommend|related|ランキング|おすすめ)", str(anchor.get("class", "")) + " " + p.path, re.I):
            continue
        if anchor.find_parent("footer") is not None:
            continue
        if anchor.find_parent(attrs={"class": re.compile(r"related|recommend|upsells|cross-sells", re.I)}) is not None:
            continue
        card = anchor if re.search(r"itemListBox|product[-_ ]?(?:card|item)|goods[-_ ]?(?:card|item)", " ".join(map(str,anchor.get("class") or [])),re.I) else anchor.find_parent(attrs={"itemtype": re.compile(r"schema.org/Product")})
        if card is None:
            card = anchor.find_parent(class_=re.compile(r"(?:itemListBox|product[-_ ]?(?:card|item)|goods[-_ ]?(?:card|item))", re.I))
        if card is not None and card.parent is not None and card.parent.name == "article":
            card = card.parent
        label = anchor.get_text(" ", strip=True) + " " + " ".join(str(x.get("alt") or "") for x in anchor.find_all("img"))
        local = card.get_text(" ", strip=True)[:700] if card is not None else label
        matched = any(x in _key(local) for x in names)
        same_host = (p.hostname or "").casefold() == page_host
        explicit_shop = bool(re.search(r"公式.{0,8}(?:通販|ショップ|ストア)|official.{0,8}(?:shop|store)|官方.{0,8}(?:商店|商城|通販)", label, re.I))
        event_link = bool(re.search(r"(?:秋|夏|冬|春|周年|限定|再販|复刻|復刻).{0,20}(?:通販|通贩|グッズ|商品)|(?:goods|通販|通贩|グッズ|商品).{0,20}(?:秋|夏|冬|春|限定|周年)", label, re.I))
        seasons = [s for s in ("秋", "夏", "冬", "春") if s in news.title]
        if event_link and seasons and any(s in label for s in ("秋", "夏", "冬", "春")) and not any(s in label for s in seasons):
            continue
        if anchor.find_parent("nav") is not None and not (explicit_shop or event_link):
            continue
        if not same_host:
            if not (explicit_root and source.officiality >= .75 and explicit_shop):
                continue
            kind = "shop"
        elif card is not None and (matched or bound_page):
            kind = "goods"
        elif event_link and (matched or explicit_root):
            kind = "event"
        elif explicit_shop and (matched or explicit_root):
            kind = "shop"
        elif bound_page and source.navigation_kind in {"event", "goods"} and re.search(r"商品詳細|商品详情|詳細を見る|查看详情|product details", label, re.I):
            kind = "goods"
        else:
            continue
        # Cards with the news item named first win over other event products.
        priority = 0 if matched else 1 if kind == "event" else 2
        found.append((priority, order, url, local.strip(), kind))
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.get_text())
        except (ValueError, TypeError):
            continue
        for product in _json_products(data):
            label = str(product.get("name") or "")
            url = urljoin(page_url, str(product.get("url") or ""))
            p = urlsplit(url)
            if p.scheme in {"http", "https"} and (p.hostname or "").casefold() == page_host and (bound_page or any(x in _key(label) for x in names)):
                found.append((0, len(found), url, label, "goods"))
    seen, result = set(), []
    for _, _, url, label, kind in sorted(found, key=lambda row: row[:2]):
        if url not in seen:
            seen.add(url)
            result.append((url, label[:700], kind))
    return result
