"""Steam localization screenshot extraction (never consumes trailer media)."""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..domain import (CollectionContext, CollectionResult, FailureRecord, FailureStage,
                      ImageEvidence, LocalizationProvenance, NewsItem, SourceRef, SourceType)


_APP = re.compile(r"/app/(\d+)(?:/|$)")


class SteamLocalizationAdapter:
    def __init__(self, http_client):
        self.http = http_client

    @staticmethod
    def _props(page: str) -> list[dict]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(page, "html.parser")
        values = []
        for node in soup.select('[data-featuretarget="gamehighlight-desktopcarousel"][data-props]'):
            try:
                item = json.loads(node.get("data-props", "{}"))
                if isinstance(item, dict):
                    values.append(item)
            except (TypeError, ValueError):
                continue
        # Legacy store DOM: the anchor href is the linked full screenshot;
        # data-screenshotid is retained as item-local source evidence.
        for node in soup.select("a.highlight_screenshot_link[data-screenshotid]"):
            href = node.get("href")
            if href:
                values.append({"screenshots": [{"full": href, "thumbnail": node.select_one("img").get("src")
                              if node.select_one("img") else None,
                              "altText": node.select_one("img").get("alt", "") if node.select_one("img") else "",
                              "screenshotid": node.get("data-screenshotid")} ]})
        # Older Steam pages also carry a URL array in inline JavaScript.
        if not values:
            for script in soup.find_all("script"):
                raw = script.string or script.get_text()
                legacy = re.search(r"(?:rgScreenshotURLs|screenshotURLs)\s*=\s*(\[[\s\S]*?\])", raw)
                if legacy:
                    try:
                        urls = json.loads(legacy.group(1))
                        if isinstance(urls, list):
                            values.append({"screenshots": [{"full": url, "name": url} for url in urls if isinstance(url, str)]})
                    except (TypeError, ValueError):
                        pass
                if "gamehighlight-desktopcarousel" not in raw and '"screenshots"' not in raw:
                    continue
                for match in re.findall(r"\{[^{}]*\"screenshots\"\s*:\s*\[[^\]]*\][^{}]*\}", raw):
                    try:
                        item = json.loads(match)
                        if isinstance(item, dict):
                            values.append(item)
                    except (TypeError, ValueError):
                        pass
        return values

    def collect(self, news: NewsItem, source: SourceRef, context: CollectionContext) -> CollectionResult:
        try:
            url_app = _APP.search(urlsplit(source.url).path)
            declared = list(dict.fromkeys(str(value) for value in
                (news.localization_context.steam_app_ids if news.localization_context else [])))
            app_id = url_app.group(1) if url_app else (declared[0] if len(declared) == 1 else "")
            source_host = (urlsplit(source.url).hostname or "").casefold()
            source_parts = urlsplit(source.url)
            if (source_host != "store.steampowered.com" or source_parts.scheme != "https"
                    or source_parts.username or source_parts.password or source_parts.port not in {None, 443}):
                return CollectionResult()
            if app_id and declared and app_id not in declared:
                return _failure(news, source, "steam_app_id_conflict", "Steam 来源 AppID 与新闻绑定 AppID 不一致。")
            if not app_id:
                return CollectionResult()
            request_url = source.url if url_app else f"https://store.steampowered.com/app/{app_id}/"
            response = self.http.get(request_url)
            status = int(getattr(response, "status_code", 200))
            if not 200 <= status < 300:
                return _http_failure(news, source, status)
            page_url = str(getattr(response, "url", request_url) or request_url)
            final_parts = urlsplit(page_url)
            final_app = _APP.search(final_parts.path)
            if (final_parts.hostname or "").casefold() != "store.steampowered.com" or not final_app or final_app.group(1) != app_id:
                return _failure(news, source, "steam_redirect_rejected", "Steam 页面发生跨主机跳转，已拒绝。")
            props = self._props(getattr(response, "text", "") or "")
            if not props:
                parts = urlsplit(request_url)
                query = dict(parse_qsl(parts.query, keep_blank_values=True))
                if query.get("l") != "english":
                    query["l"] = "english"
                    fallback_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
                    response = self.http.get(fallback_url)
                    status = int(getattr(response, "status_code", 200))
                    if not 200 <= status < 300:
                        return _http_failure(news, source, status)
                    page_url = str(getattr(response, "url", fallback_url) or fallback_url)
                    final_parts = urlsplit(page_url)
                    final_app = _APP.search(final_parts.path)
                    if (final_parts.hostname or "").casefold() != "store.steampowered.com" or not final_app or final_app.group(1) != app_id:
                        return _failure(news, source, "steam_redirect_rejected", "Steam 回退页面发生跨主机跳转，已拒绝。")
                    props = self._props(getattr(response, "text", "") or "")
            screenshots = []
            for data in props:
                for shot in data.get("screenshots", []) if isinstance(data.get("screenshots", []), list) else []:
                    if not isinstance(shot, dict):
                        continue
                    full = shot.get("full")
                    standard = shot.get("standard")
                    named = shot.get("name")
                    thumb = shot.get("thumbnail")
                    if full and _steam_image(str(full), app_id):
                        screenshots.append((str(full), _resolution(str(full), shot, "full"), shot))
                    if standard and standard != full and _steam_image(str(standard), app_id):
                        screenshots.append((str(standard), "standard", shot))
                    if named and named not in {full, standard} and _steam_image(str(named), app_id):
                        screenshots.append((str(named), "unknown", shot))
                    if thumb and thumb not in {full, standard, named} and _steam_image(str(thumb), app_id):
                        screenshots.append((str(thumb), "thumbnail", shot))
            seen = set()
            candidates = []
            # Preserve carousel order within each resolution tier. A limited
            # candidate budget must never discard an original in favour of
            # an earlier item's thumbnail or standard-sized preview.
            tiers = {"full": 0, "standard": 1, "thumbnail": 2, "unknown": 3}
            screenshots.sort(key=lambda item: tiers.get(item[1], 3))
            explicit_apps = set()
            for original_source in news.source_urls:
                parsed_source = urlsplit(original_source)
                match = _APP.search(parsed_source.path)
                if parsed_source.hostname == "store.steampowered.com" and match:
                    explicit_apps.add(match.group(1))
            binding = "reference" if len(explicit_apps) > 1 and len(declared) != 1 else "confirmed"
            for image_url, resolution, shot in screenshots:
                if image_url in seen or len(candidates) >= context.max_candidates:
                    continue
                seen.add(image_url)
                candidate = _candidate(news, image_url, source, context)
                candidate.news_source_url = source.root_url or source.url
                candidate.source_type = SourceType.STEAM
                candidate.image_alt = str(shot.get("altText") or "") or None
                dims = shot.get("thumbnail_dims") if resolution == "thumbnail" else shot.get("dims")
                if not isinstance(dims, list):
                    dims = [shot.get("width"), shot.get("height")]
                candidate.expected_width = _positive_dim(dims[0]) if len(dims) > 0 else None
                candidate.expected_height = _positive_dim(dims[1]) if len(dims) > 1 else None
                candidate.evidence = [ImageEvidence(page_url=page_url, container="steam:gamehighlight-desktopcarousel",
                    item_id=str(shot.get("screenshotid") or app_id), role="gameplay_screenshot", text=candidate.image_alt or "", method="dom",
                    relationship=f"Steam screenshot {resolution}",
                    variant_of=str(shot.get("full")) if resolution != "full" and shot.get("full") else None)]
                candidate.localization_provenance = LocalizationProvenance(source="steam", work_id=app_id,
                    binding=binding, resolution=resolution, screenshot=True, native=(resolution == "full"),
                    source_chain=list(dict.fromkeys([source.root_url or source.url, source.url, page_url])))
                candidate.signals.update({"localization_source": "steam", "localization_work_id": app_id,
                    "localization_binding": binding, "localization_resolution": resolution,
                    "localization_screenshot": True, "localization_native": resolution == "full"})
                if resolution != "full" or binding != "confirmed":
                    candidate.signals["localization_review_only"] = True
                candidates.append(candidate)
            if not candidates:
                return _failure(news, source, "steam_no_screenshots", "Steam 当前 App 页面未提供可验证的截图。")
            return CollectionResult(candidates=candidates)
        except Exception as exc:
            if type(exc).__name__ in {"CancellationRequested", "CancelledError"}:
                raise
            return _failure(news, source, "steam_collection_failed", f"Steam 页面采集失败（{type(exc).__name__}）。")


def _positive_dim(value):
    try:
        number = int(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _candidate(news, url, source, context):
    from ..discovery.adapters.common import _candidate as make_candidate
    return make_candidate(news, url, source, context)


def _steam_image(url, app_id):
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").casefold()
    return (parts.scheme == "https" and (host == "steamstatic.com" or host.endswith(".steamstatic.com"))
            and re.search(rf"/(?:store_item_assets/steam/apps|steam/apps)/{re.escape(app_id)}/", parts.path) is not None
            and not parts.username and not parts.password)


def _resolution(url, shot, declared):
    dims = shot.get("dims")
    if isinstance(dims, list) and len(dims) >= 2:
        width, height = _positive_dim(dims[0]), _positive_dim(dims[1])
        if width and height and (width < 800 or height < 450):
            return "unknown"
    match = re.search(r"(?:^|[._-])(\d{2,4})x(\d{2,4})(?:[._-]|$)", urlsplit(url).path, re.I)
    if match and (int(match.group(1)) < 800 or int(match.group(2)) < 450):
        return "unknown"
    return declared


def _failure(news, source, code, message):
    retryable = code not in {"steam_app_id_conflict", "steam_redirect_rejected"}
    return CollectionResult(failures=[FailureRecord(stage=FailureStage.COLLECT, news_id=news.id,
        code=code, message=message, source_url=source.url, retryable=retryable)])


def _http_failure(news, source, status):
    return CollectionResult(failures=[FailureRecord(stage=FailureStage.COLLECT, news_id=news.id,
        code="steam_page_http_error", message=f"Steam 页面返回 HTTP {status}。", source_url=source.url,
        retryable=status in {408, 429} or status >= 500)])
