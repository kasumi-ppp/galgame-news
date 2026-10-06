"""HTML-backed source adapters."""

from __future__ import annotations

import json
import re
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ...domain import CollectionContext, CollectionResult, FailureRecord, FailureStage, ReviewReason
from ...video.discovery import discover_html_video_urls
from ..http import SafeHttpClient
from .common import (
    _candidate,
    _image_priority,
    _looks_like_image_url,
    _srcset_largest,
    _upgrade_image_url,
    _video_candidates,
)


def _explicit_age_confirmation_cookie(soup: BeautifulSoup, html: str) -> str | None:
    """Recognize the publisher's explicit 18+ action and its cookie effect."""
    confirmation = soup.find(id="confirm-yes")
    if confirmation is None or not re.search(r"18", confirmation.get_text(" ", strip=True)):
        return None
    match = re.search(
        r"Cookies\s*\.\s*set\s*\(\s*(['\"])PermitRate\1\s*,\s*(['\"])18\2",
        html,
        flags=re.I,
    )
    return "18" if match else None


def _is_age_gate(soup: BeautifulSoup, html: str) -> bool:
    lowered = html.casefold()
    return bool(
        soup.find("meta", attrs={"name": lambda value: value and any(
            token in value.casefold().replace("-", " ").split()
            for token in ("age", "adult", "verification")
        )})
        or "age-verification" in lowered
        or "age gate" in lowered
        or soup.find(id="confirm-yes") is not None
    )


class OfficialHtmlAdapter:
    def __init__(self, *, transport: Callable[..., Any] | None = None, client: SafeHttpClient | None = None):
        self.client = client or SafeHttpClient(transport=transport, timeout=20.0, max_retries=2)

    def collect(self, news_item, source_ref, context: CollectionContext) -> CollectionResult:
        try:
            response = self.client.get(source_ref.url)
            page_url = str(getattr(response, "url", source_ref.url) or source_ref.url)
            html = getattr(response, "text", "") or ""
            soup = BeautifulSoup(html, "html.parser")
            confirmed_age_gate = False
            confirmation_cookie = _explicit_age_confirmation_cookie(soup, html)
            if confirmation_cookie:
                response = self.client.get(page_url, cookies={"PermitRate": confirmation_cookie})
                page_url = str(getattr(response, "url", page_url) or page_url)
                html = getattr(response, "text", "") or ""
                soup = BeautifulSoup(html, "html.parser")
                confirmed_age_gate = not _is_age_gate(soup, html)
            if _is_age_gate(soup, html):
                source_ref.requires_review = True
                source_ref.review_reasons = list(dict.fromkeys([*source_ref.review_reasons, ReviewReason.AGE_GATE]))
                return CollectionResult(manual_review_reasons=[ReviewReason.AGE_GATE])
            urls: list[str] = []
            page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
            if not page_title:
                for meta in soup.find_all("meta"):
                    key = (meta.get("property") or meta.get("name") or "").casefold()
                    if key in {"og:title", "twitter:title"} and meta.get("content"):
                        page_title = str(meta["content"]).strip()
                        break
            image_context: dict[str, list[tuple[str, str]]] = {}
            image_gallery_context: dict[str, list[str]] = {}
            image_variant_context: dict[str, str] = {}
            image_exclusions: dict[str, str] = {}
            video_urls = discover_html_video_urls(html, page_url)

            def excluded_role(tag) -> str | None:
                # An explicit local image role takes precedence over a gallery
                # ancestor. Do not use asset filenames or the page title here.
                for node in [tag, *list(tag.parents)]:
                    if getattr(node, "name", None) in {"html", "body", "head", "[document]"}:
                        continue
                    if getattr(node, "name", None) in {"header", "footer", "nav"}:
                        return str(node.name)
                    tokens = " ".join([
                        str(node.get("id") or ""), *map(str, node.get("class") or []),
                        str(node.get("data-section") or ""),
                        str(node.get("alt") or "") if node is tag else "",
                    ])
                    match = re.search(
                        r"(?:^|[\s_-])(logo|header|footer|nav|navbar|navigation|character|chara|goods|merchandise|special|campaign|package|news)(?:$|[\s_-])|ロゴ|キャラクター|グッズ",
                        tokens, re.I,
                    )
                    if match:
                        return match.group(1) or match.group(0)
                return None

            def gallery_scoped(tag, anchor=None) -> str | None:
                if excluded_role(tag):
                    return None
                nodes = [tag, *list(tag.parents)]
                if anchor is not None:
                    nodes.append(anchor)
                for node in nodes:
                    if getattr(node, "name", None) in {"html", "body", "head", "[document]"}:
                        continue
                    tokens = [str(node.get("id") or ""), *map(str, node.get("class") or []), str(node.get("data-section") or "")]
                    if any(re.match(r"^(?:gallery|graphic)(?:$|[_-])", token, re.I) or token in {"ギャラリー", "画像一覧"} for token in tokens):
                        label = node.name + ("#" + str(node["id"]) if node.get("id") else "".join("." + str(value) for value in node.get("class") or []))
                        return "dom:" + label
                return None

            def remember_context(raw_url: str, tag, anchor=None, *, relationship="image attribute", reference=None, evidence_tag=None) -> None:
                if not isinstance(raw_url, str):
                    return
                normalized = _upgrade_image_url(urljoin(page_url, raw_url))
                context_tag = evidence_tag if evidence_tag is not None else tag
                alt = str(tag.get("alt") or context_tag.get("alt") or "").strip()
                nearby = context_tag.parent.get_text(" ", strip=True) if getattr(context_tag, "parent", None) else ""
                nearby = re.sub(r"\s+", " ", nearby)[:500]
                if normalized and (alt or nearby):
                    image_context.setdefault(normalized, []).append((alt, nearby))
                if normalized:
                    if reference and normalized != reference:
                        image_variant_context.setdefault(normalized, reference)
                    role = excluded_role(tag)
                    if role:
                        image_exclusions[normalized] = role
                    gallery_source = gallery_scoped(evidence_tag if evidence_tag is not None else tag, anchor)
                    if gallery_source and not role:
                        image_gallery_context.setdefault(normalized, []).append(f"{gallery_source} via {relationship}")

            image_attrs = ("data-original", "data-full", "data-large", "data-hires", "data-zoom-image", "src", "data-src", "data-lazy-src")

            def image_variants(tag) -> list[tuple[str, str]]:
                variants = [(str(tag[attr]), f"img[{attr}]") for attr in image_attrs if tag.get(attr)]
                for attr in ("srcset", "data-srcset"):
                    if tag.get(attr):
                        largest = _srcset_largest(str(tag[attr]))
                        if largest:
                            variants.append((largest, f"img[{attr}]"))
                picture = tag.find_parent("picture")
                if picture is not None and len(picture.find_all("img")) == 1:
                    for source in picture.find_all("source"):
                        for attr in ("srcset", "data-srcset"):
                            if source.get(attr):
                                largest = _srcset_largest(str(source[attr]))
                                if largest:
                                    variants.append((largest, f"picture/source[{attr}]"))
                return variants

            for anchor in soup.find_all("a", href=True):
                href = anchor["href"]
                if _looks_like_image_url(href) or (anchor.find("img") is not None and any(token in urlsplit(href).path.casefold() for token in ("gallery", "cg", "image", "media"))):
                    urls.append(href)
            for tag in soup.find_all("meta"):
                key = (tag.get("property") or tag.get("name") or "").casefold()
                if key in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"} and tag.get("content"):
                    urls.append(tag["content"])
            for tag in soup.find_all("img"):
                variants = image_variants(tag)
                anchor = tag.find_parent(["a", "button"])
                linked_full = None
                if anchor is not None and len(anchor.find_all("img")) == 1:
                    href = str(anchor.get("href") or "")
                    if _looks_like_image_url(href):
                        linked_full = href
                        variants.append((href, "anchor[href]"))
                # Static references to one modal image are an explicit
                # relationship even when the full image lives elsewhere.
                for trigger in (tag, tag.parent, anchor):
                    if trigger is None or (trigger is not tag and len(trigger.find_all("img")) != 1):
                        continue
                    for attr in ("href", "data-target", "data-bs-target", "data-modal", "data-izimodal-open", "aria-controls"):
                        selector = str(trigger.get(attr) or "")
                        target_id = selector.removeprefix("#") if selector.startswith("#") or attr == "aria-controls" else ""
                        target = soup.find(id=target_id) if target_id else None
                        target_images = target.find_all("img") if target is not None else []
                        if len(target_images) == 1:
                            for value, relation in image_variants(target_images[0]):
                                reference = _upgrade_image_url(urljoin(page_url, str(tag.get("src") or tag.get("data-src") or variants[0][0]))) if variants else None
                                trigger_name = "anchor" if trigger.name == "a" else trigger.name
                                remember_context(value, target_images[0], relationship=f"{trigger_name}[{attr}] -> #{target_id}/{relation}", reference=reference, evidence_tag=tag)
                                urls.append(value)
                # Full-size attributes on a one-image wrapper refer to this
                # item; shared containers with several images are ambiguous.
                for wrapper in (tag.parent, anchor):
                    if wrapper is not None and len(wrapper.find_all("img")) == 1:
                        for attr in image_attrs[:5]:
                            if wrapper.get(attr):
                                variants.append((str(wrapper[attr]), f"{wrapper.name}[{attr}]"))
                reference = _upgrade_image_url(urljoin(page_url, str(tag.get("src") or tag.get("data-src") or variants[0][0]))) if variants else None
                for value, relationship in variants:
                    remember_context(value, tag, anchor, relationship=relationship, reference=reference)
                    if not (linked_full and value in {tag.get(attr) for attr in ("src", "data-src", "data-lazy-src")}):
                        urls.append(value)
            for tag in soup.find_all("source"):
                for attr in ("srcset", "data-srcset"):
                    if tag.get(attr):
                        largest = _srcset_largest(str(tag[attr]))
                        if largest:
                            urls.append(largest)
                            picture = tag.find_parent("picture")
                            # A fallback img already provided this source's
                            # image-local context, including any excluded role.
                            if picture is None or picture.find("img") is None or excluded_role(tag):
                                remember_context(largest, tag, relationship=f"source[{attr}]")
            for tag in soup.find_all(style=True):
                urls.extend(re.findall(r"url\(\s*['\"]?([^'\")]+)", tag.get("style", ""), flags=re.I))
            for tag in soup.find_all(True):
                for attr in ("data-background", "data-bg", "data-image"):
                    if tag.get(attr):
                        urls.append(tag[attr])
            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string or script.get_text())
                    values = data.get("image", []) if isinstance(data, dict) else []
                    urls.extend(values if isinstance(values, list) else [values])
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue

            def collect_json_images(value: Any) -> None:
                if isinstance(value, str):
                    if value.startswith(("http://", "https://", "/", "wix:image://v1/")):
                        urls.append(value)
                elif isinstance(value, dict):
                    for child in value.values():
                        collect_json_images(child)
                elif isinstance(value, list):
                    for child in value:
                        collect_json_images(child)

            for script in soup.find_all("script"):
                text = script.string or script.get_text()
                if script.get("type") == "application/json" or script.get("id") == "__NEXT_DATA__" or "wix-warmup" in (script.get("id") or "").casefold() or "wix-viewer" in (script.get("id") or "").casefold():
                    try:
                        collect_json_images(json.loads(text))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        pass
            embedded = html.replace(r"\/", "/")
            urls.extend(re.findall(r"https://static\.wixstatic\.com/media/[^\"'<>\\\s]+", embedded))
            urls.extend(re.findall(r"wix:image://v1/[^\"'<>\\\s]+", embedded))
            candidates = []
            seen = set()
            for value in urls:
                if not isinstance(value, str):
                    continue
                image_url = _upgrade_image_url(urljoin(page_url, value))
                if not image_url or image_url in seen or urlsplit(image_url).scheme not in {"http", "https"}:
                    continue
                if not _looks_like_image_url(image_url):
                    continue
                seen.add(image_url)
                candidate = _candidate(news_item, image_url, source_ref, context)
                if page_title:
                    candidate.signals["page_title"] = page_title
                if image_context.get(image_url):
                    candidate.image_alt = " ".join(dict.fromkeys(alt for alt, _ in image_context[image_url] if alt)) or None
                    candidate.nearby_text = " ".join(dict.fromkeys(text for _, text in image_context[image_url] if text)) or None
                    if candidate.image_alt:
                        candidate.signals["alt"] = candidate.image_alt
                    if candidate.nearby_text:
                        candidate.signals["nearby_text"] = candidate.nearby_text
                if image_url in image_variant_context:
                    candidate.signals["media_variant_of"] = image_variant_context[image_url]
                if image_url in image_exclusions:
                    candidate.signals["gallery_excluded_reason"] = image_exclusions[image_url]
                elif image_url in image_gallery_context:
                    candidate.signals["gallery_path"] = True
                    candidate.signals["gallery_evidence_source"] = "; ".join(dict.fromkeys(image_gallery_context[image_url]))
                if confirmed_age_gate:
                    candidate.signals["age_gate_confirmed"] = True
                candidates.append(candidate)
            if not candidates and not video_urls and soup.find("script"):
                source_ref.requires_review = True
                source_ref.review_reasons = list(dict.fromkeys([*source_ref.review_reasons, ReviewReason.DYNAMIC_PAGE]))
                return CollectionResult(manual_review_reasons=[ReviewReason.DYNAMIC_PAGE])
            candidates.sort(key=lambda candidate: _image_priority(candidate.image_url))
            return CollectionResult(candidates=candidates[:context.max_candidates], video_candidates=_video_candidates(news_item, source_ref, video_urls, page_title))
        except Exception as exc:
            return CollectionResult(failures=[FailureRecord(stage=FailureStage.COLLECT, news_id=news_item.id, code="adapter_error", message=str(exc), source_url=source_ref.url, retryable=True)])


class SteamAdapter(OfficialHtmlAdapter):
    """Steam pages use the same HTML metadata plus screenshot links."""


class DynamicPageAdapter(OfficialHtmlAdapter):
    """Metadata-only adapter for JavaScript pages; always requests review."""

    def collect(self, news_item, source_ref, context: CollectionContext) -> CollectionResult:
        result = super().collect(news_item, source_ref, context)
        source_ref.requires_review = True
        source_ref.review_reasons = list(dict.fromkeys([*source_ref.review_reasons, ReviewReason.DYNAMIC_PAGE]))
        result.manual_review_reasons = list(dict.fromkeys([*result.manual_review_reasons, ReviewReason.DYNAMIC_PAGE]))
        return result
