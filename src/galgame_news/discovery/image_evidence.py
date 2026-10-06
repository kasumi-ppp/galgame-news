"""Small, image-local evidence extractors for static HTML pages."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ..domain import ImageEvidence


_DOCUMENT_NODES = {"html", "body", "head", "[document]"}


def _selector(node: Any) -> str:
    name = str(getattr(node, "name", "") or "element")
    value = name
    if getattr(node, "get", None):
        if node.get("id"):
            value += "#" + str(node.get("id"))
        classes = node.get("class") or []
        value += "".join("." + str(part) for part in classes)
    return value


def _text(node: Any) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True))[:400]


def evidence_for_image(
    tag: Any,
    soup: BeautifulSoup,
    page_url: str,
    *,
    method: str = "dom",
    relationship: str = "image attribute",
    variant_of: str | None = None,
) -> ImageEvidence | None:
    """Describe one image using only its nearest meaningful DOM relationship."""
    if tag is None:
        return None
    parents = [tag, *list(getattr(tag, "parents", []))]
    for node in parents:
        if getattr(node, "name", None) in _DOCUMENT_NODES:
            continue
        tokens = " ".join([str(node.get("id") or ""), *map(str, node.get("class") or [])])
        if re.search(r"(?:^|[\s_-])(?:related|recommend(?:ed|ation)?|upsells|cross-sells)(?:$|[\s_-])", tokens, re.I):
            return ImageEvidence(page_url=page_url, container=_selector(node), role="decorative",
                                 text="推荐商品区域", method=method, relationship=relationship, variant_of=variant_of)
    own_text = " ".join([str(tag.get("alt") or ""), str(tag.get("id") or ""), *map(str, tag.get("class") or [])])
    for pattern, role in ((r"(?:^|[\s_-])logo(?:$|[\s_-])|ロゴ", "logo"),
                          (r"(?:^|[\s_-])(?:character|chara|sprite|tachie)(?:$|[\s_-])|立绘|立繪|キャラクター", "character_art"),
                          (r"(?:^|[\s_-])(?:cover|package|jacket)(?:$|[\s_-])", "cover"),
                          (r"(?:^|[\s_-])(?:banner|bnr)(?:$|[\s_-])|横幅|バナー", "banner"),
                          (r"(?:^|[\s_-])(?:icon|btn|button|control)(?:$|[\s_-])", "ui")):
        if re.search(pattern, own_text, re.I):
            return ImageEvidence(page_url=page_url, container=_selector(tag), role=role,
                                 text=own_text[:400], method=method, relationship=relationship, variant_of=variant_of)
    # Headings, navigation, controls, separators and branded UI are content
    # decoration even when nested inside a gallery section.
    for node in parents:
        if getattr(node, "name", None) in _DOCUMENT_NODES:
            continue
        thumbnail_button = node.name == "button" and (node.find("img") is not None) and bool(
            node.get("data-target") or node.get("data-bs-target") or node.get("data-modal") or
            re.search(r"gallery|thumb", " ".join(map(str, node.get("class") or [])), re.I))
        if node.name in {"h1", "h2", "h3", "h4", "h5", "h6", "nav", "header", "footer"} or (node.name == "button" and not thumbnail_button):
            return ImageEvidence(
                page_url=page_url, container=_selector(node), role="decorative",
                text=_text(node), method=method, relationship=relationship, variant_of=variant_of,
            )
        tokens = " ".join([str(node.get("id") or ""), *map(str, node.get("class") or [])])
        if re.search(r"(?:^|[\s_-])(?:header|footer|nav|navbar|navigation|heading|title)(?:$|[\s_-])", tokens, re.I):
            return ImageEvidence(page_url=page_url, container=_selector(node), role="decorative", text=_text(node),
                                 method=method, relationship=relationship, variant_of=variant_of)
        if re.search(r"(?:^|[\s_-])(logo|separator|divider|arrow|control|navigation|navbar)(?:$|[\s_-])", tokens, re.I):
            return ImageEvidence(
                page_url=page_url, container=_selector(node), role="decorative",
                text=_text(node), method=method, relationship=relationship, variant_of=variant_of,
            )

    # Product evidence is deliberately restricted to a card with a local
    # title and price. A broad page heading or a nearby unrelated paragraph
    # cannot assign a goods role.
    cards = [node for node in parents if getattr(node, "name", None) and
             (re.search(r"itemListBox|product[-_ ]?(?:card|item|detail)|goods[-_ ]?(?:card|item|detail)", " ".join(map(str, node.get("class") or [])), re.I)
              or str(node.get("itemtype") or "").endswith("schema.org/Product"))]
    for card in cards:
        title = card.select_one(".loop-title, [itemprop='name'], .product-title, .product-card-product-name, .product-name, .goods-title")
        price = card.select_one(".price, [itemprop='price'], .woocommerce-Price-amount, .kakaku_list, .product-price-display-value, .product-card-price")
        if title is not None and price is not None:
            link = card if card.name == "a" and card.get("href") else card.find("a", href=True)
            work = card.select_one(".c333")
            return ImageEvidence(
                page_url=page_url, container=_selector(card),
                item_id=urlsplit(str(link.get("href"))).path.strip("/") if link else "",
                role="goods", text=" ".join(filter(None, (_text(work), _text(title)))), method=method, relationship=relationship,
                variant_of=variant_of,
            )
    for node in parents:
        if getattr(node, "name", None) in _DOCUMENT_NODES:
            continue
        tokens = " ".join([str(node.get("id") or ""), *map(str, node.get("class") or []), str(tag.get("alt") or "") if node is tag else ""])
        if re.search(r"(?:^|[\s_-])(?:goods|merchandise|character|chara|special|campaign|package|news)(?:$|[\s_-])", tokens, re.I):
            # An excluded section cannot donate CG evidence. A real product
            # card above provides its own positive goods evidence instead.
            return ImageEvidence(page_url=page_url, container=_selector(node), role="unknown", text=str(tag.get("alt") or "")[:400],
                                 method=method, relationship=relationship, variant_of=variant_of)

    # Some older Key pages bind gallery groups to a tab via a numbered
    # lightbox class. The tab label carries the image kind and wins over a
    # generic image alt such as "サンプルCG".
    group = next((str(value) for node in parents for value in (node.get("class") or [])
                  if re.fullmatch(r"group\d+", str(value), re.I)), "")
    if group:
        number = re.search(r"\d+", group).group(0)
        box = next((node for node in parents if f"gallery_cg_box_{number}" in (node.get("class") or [])), None)
        if box is not None:
            tab = soup.find(id=f"g_{number}_bt")
            label = _text(tab)
            role = "background_art" if re.search(r"背景|background", label, re.I) else "game_cg" if re.search(r"CG|イベント|event|scene", label, re.I) else "unknown"
            return ImageEvidence(
                page_url=page_url, container=_selector(box), item_id=group,
                role=role, text=label, method=method, relationship=relationship,
                variant_of=variant_of,
            )

    # Generic gallery containers identify only actual gallery content. Images
    # inside section headings were handled above as decorative.
    gallery = next((node for node in parents if getattr(node, "name", None) not in _DOCUMENT_NODES and
                    re.search(r"(?:^|[\s_-])(gallery|graphic)(?:$|[\s_-])",
                              " ".join([str(node.get("id") or ""), *map(str, node.get("class") or [])]), re.I)), None)
    if gallery is not None:
        label = ""
        node_id = gallery.get("id")
        if node_id:
            label = " ".join(_text(node) for node in soup.find_all(True) if node.get("aria-controls") == node_id or node.get("href") == "#" + node_id)
        role = "background_art" if re.search(r"背景|background", label, re.I) else "game_cg"
        local_parent = tag.parent if getattr(tag, "parent", None) and tag.parent.name in {"a", "figure", "li", "picture"} else None
        return ImageEvidence(
            page_url=page_url, container=_selector(gallery), item_id=str(tag.get("data-index") or tag.get("src") or ""), role=role,
            text=(label or str(tag.get("alt") or "") or _text(local_parent))[:400], method=method, relationship=relationship,
            variant_of=variant_of,
        )
    return None


def evidence_for_css_image(tag: Any, soup: BeautifulSoup, page_url: str, *, method: str = "dom") -> ImageEvidence:
    local = evidence_for_image(tag, soup, page_url, method=method, relationship="CSS background-image")
    if local is not None and local.role == "goods":
        return local
    return ImageEvidence(
        page_url=page_url, container=_selector(tag), role="decorative",
        text=_text(tag), method=method, relationship="CSS background-image",
    )
