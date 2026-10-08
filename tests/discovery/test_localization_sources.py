import json
from types import SimpleNamespace

import pytest

from galgame_news.domain import CollectionContext, LocalizationContext, NewsItem, SourceRef, SourceType
from galgame_news.localization import LocalizationImageService

APP = "3419820"
STEAM_BASE = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/3419820/"


class FakeHttp:
    def __init__(self, pages=None, post_data=None):
        self.pages = pages or {}
        self.post_data = post_data or {}
        self.posts = []
        self.gets = []

    def get(self, url):
        self.gets.append(url)
        response = self.pages.get(url, self.pages.get("default", SimpleNamespace(text="", url=url, status_code=200)))
        return response

    def post_json(self, url, payload):
        self.posts.append((url, payload))
        endpoint = url.rsplit("/", 1)[-1]
        identifier = payload["filters"][2]
        data = self.post_data.get((endpoint, identifier))
        body = {"results": [data] if data else []}
        # AsyncHttpResponse has text but no .json(); test that shape explicitly.
        return SimpleNamespace(status_code=200, text=json.dumps(body), headers={})


def news(*, title="Localized game", source_urls=None, localization_context=None, body="Available now"):
    return NewsItem(issue_id="i", sequence=1, section="汉化", title=title, body=body,
        source_urls=source_urls or ["https://publisher.example/news"], localization_context=localization_context)


def steam_source(url=None):
    url = url or f"https://store.steampowered.com/app/{APP}/title/"
    return SourceRef(url=url, source_type=SourceType.STEAM, domain="store.steampowered.com")


def steam_page(screenshots, trailers=None):
    props = {"screenshots": screenshots, "trailers": trailers or []}
    props_json = json.dumps(props).replace('"', '&quot;')
    return f'<div data-featuretarget="gamehighlight-desktopcarousel" data-props="{props_json}"></div>'


def test_steam_candidate_budget_keeps_all_eight_originals():
    shots = [{"full": STEAM_BASE + f"ss_{i}.1920x1080.jpg",
              "standard": STEAM_BASE + f"ss_{i}.600x338.jpg",
              "thumbnail": STEAM_BASE + f"ss_{i}.116x65.jpg"} for i in range(8)]
    source = steam_source()
    client = FakeHttp(pages={"default": SimpleNamespace(text=steam_page(shots), url=source.url, status_code=200)})
    result = LocalizationImageService(client).collect(news(source_urls=[source.url]), source, CollectionContext(max_candidates=20))
    assert len(result.candidates) == 20
    assert all(c.localization_provenance.resolution == "full" for c in result.candidates[:8])
    assert sum(c.localization_provenance.resolution == "full" for c in result.candidates) == 8


def test_steam_multiple_unresolved_app_ids_are_reference_only():
    source = steam_source()
    client = FakeHttp(pages={"default": SimpleNamespace(text=steam_page([{"full": STEAM_BASE + "ss.jpg"}]), url=source.url, status_code=200)})
    item = news(source_urls=[source.url, "https://store.steampowered.com/app/99/"])
    result = LocalizationImageService(client).collect(item, source, CollectionContext())
    assert result.candidates[0].localization_provenance.binding == "reference"


def test_steam_full_only_means_full_and_thumbnail_is_variant_backup():
    full = STEAM_BASE + "ss_1.jpg"
    thumb = STEAM_BASE + "ss_1.600x338.jpg"
    standard = STEAM_BASE + "ss_1.1920x1080.jpg"
    html = steam_page([{"name": "ss_1.jpg", "standard": standard, "full": full, "thumbnail": thumb,
                        "thumbnail_dims": [600, 338], "dims": [1920, 1080], "altText": "In game"}],
                      [{"mp4": "https://cdn.example/trailer.mp4"}])
    client = FakeHttp(pages={"default": SimpleNamespace(text=html, url=steam_source().url, status_code=200)})
    result = LocalizationImageService(client).collect(news(localization_context=LocalizationContext(steam_app_ids=[APP])),
                                                      steam_source(), CollectionContext())
    assert [c.localization_provenance.resolution for c in result.candidates] == ["full", "standard", "thumbnail"]
    assert result.candidates[0].localization_provenance.native is True
    assert result.candidates[1].localization_provenance.native is False
    assert result.candidates[2].expected_width == 600 and result.candidates[2].expected_height == 338
    assert result.candidates[2].evidence[0].variant_of == full
    assert all("trailer" not in c.image_url for c in result.candidates)


def test_steam_rejects_cross_app_images_name_only_and_conflicting_appid():
    html = steam_page([{"full": "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/99/ss.jpg"},
                       {"name": STEAM_BASE + "ss.jpg"}])
    client = FakeHttp(pages={"default": SimpleNamespace(text=html, url=steam_source().url, status_code=200)})
    context = LocalizationContext(steam_app_ids=[APP])
    result = LocalizationImageService(client).collect(news(localization_context=context), steam_source(), CollectionContext())
    assert len(result.candidates) == 1
    assert result.candidates[0].localization_provenance.resolution == "unknown"
    assert result.candidates[0].localization_provenance.native is False
    conflict = LocalizationImageService(client).collect(news(localization_context=LocalizationContext(steam_app_ids=["123"])),
        steam_source(), CollectionContext())
    assert conflict.failures[0].code == "steam_app_id_conflict"


def test_steam_legacy_highlight_anchor_and_english_fallback():
    full = STEAM_BASE + "legacy.jpg"
    thumb = STEAM_BASE + "legacy.600x338.jpg"
    source = steam_source()
    old_html = f'<a class="highlight_screenshot_link" data-screenshotid="s42" href="{full}"><img src="{thumb}"></a>'
    fallback_url = source.url + "?l=english"
    client = FakeHttp(pages={source.url: SimpleNamespace(text="", url=source.url, status_code=200),
                             fallback_url: SimpleNamespace(text=old_html, url=fallback_url, status_code=200)})
    result = LocalizationImageService(client).collect(news(localization_context=LocalizationContext(steam_app_ids=[APP])),
        source, CollectionContext())
    assert result.candidates[0].image_url == full
    assert result.candidates[0].evidence[0].item_id == "s42"
    assert len(client.gets) == 2


def _vndb_data(*, title="Work", screenshots=None, relations=None):
    return {"id": "v7", "title": title, "alttitle": "作品", "titles": [{"title": title, "latin": title}],
        "screenshots": screenshots if screenshots is not None else [{"id": "sf1", "url": "https://t.vndb.org/sf/1/1.jpg",
            "dims": [1600, 900], "thumbnail": "https://t.vndb.org/sf/1/1_thumb.jpg", "thumbnail_dims": [250, 141],
            "release": {"id": "r12"}}], "relations": relations or []}


def test_vndb_release_vns_relation_and_release_id_fields_match_official_kana_schema():
    release = {"id": "r12", "title": "Local release", "vns": [{"id": "v7", "title": "Localized game", "alttitle": "作品", "rtype": "complete"}]}
    client = FakeHttp(post_data={("release", "r12"): release, ("vn", "v7"): _vndb_data()})
    item = news(source_urls=["https://publisher.example/news", "https://vndb.org/r12"])
    source = SourceRef(url="https://vndb.org/r12", domain="vndb.org")
    result = LocalizationImageService(client).collect(item, source, CollectionContext())
    assert [c.localization_provenance.resolution for c in result.candidates] == ["full", "thumbnail"]
    assert result.candidates[0].expected_width == 1600
    assert result.candidates[1].expected_width == 250
    assert result.candidates[1].evidence[0].variant_of == result.candidates[0].image_url
    assert result.candidates[0].evidence[0].item_id == "sf1"
    assert result.candidates[0].signals["localization_screenshot_release_id"] == "r12"
    assert result.candidates[0].localization_provenance.binding == "confirmed"
    assert len(client.posts) == 2


def test_vndb_does_not_regex_ids_from_body_or_trust_context_without_vndb_url():
    client = FakeHttp(post_data={("vn", "v7"): _vndb_data()})
    item = news(body="We mention v7 and r12 in prose.", localization_context=LocalizationContext(vndb_ids=["v7", "r12"]))
    result = LocalizationImageService(client).collect(item, SourceRef(url="https://vndb.org/", domain="vndb.org"), CollectionContext())
    assert not result.candidates and not client.posts


def test_vndb_multiple_linked_vns_need_unique_title_alias_or_stay_reference():
    release = {"id": "r12", "vns": [{"id": "v7", "title": "Other", "alttitle": "別", "rtype": "complete"},
                                    {"id": "v8", "title": "Localized game", "alttitle": "作品", "rtype": "complete"}]}
    client = FakeHttp(post_data={("release", "r12"): release, ("vn", "v8"): {**_vndb_data(), "id": "v8"},
                                 ("vn", "v7"): {**_vndb_data(), "id": "v7", "screenshots": []}})
    source = SourceRef(url="https://vndb.org/r12", domain="vndb.org")
    result = LocalizationImageService(client).collect(news(title="Localized game"), source, CollectionContext())
    assert result.candidates and all(c.localization_provenance.work_id == "v8" for c in result.candidates)
    assert all(c.localization_provenance.binding == "confirmed" for c in result.candidates)


def test_vndb_direct_relations_are_one_hop_reference_only_and_sorted():
    related = lambda ident: {"id": ident, "title": ident, "alttitle": "", "screenshots": [], "relations": []}
    base = _vndb_data(relations=[{"id": "v9", "title": "Nine", "alttitle": ""},
                                 {"id": "v8", "title": "Eight", "alttitle": ""}])
    client = FakeHttp(post_data={("vn", "v7"): base, ("vn", "v8"): related("v8"), ("vn", "v9"): related("v9")})
    source = SourceRef(url="https://vndb.org/v7", domain="vndb.org")
    result = LocalizationImageService(client).collect(news(), source, CollectionContext())
    assert [entry[1]["filters"][2] for entry in client.posts] == ["v7", "v8", "v9"]
    assert not any(c.localization_provenance.binding == "confirmed" and c.localization_provenance.work_id in {"v8", "v9"}
                   for c in result.candidates)


def test_vndb_bad_http_or_json_returns_failure_record():
    source = SourceRef(url="https://vndb.org/v7", domain="vndb.org")
    class BadHttp(FakeHttp):
        def post_json(self, url, payload):
            return SimpleNamespace(status_code=503, text="{}", headers={})
    result = LocalizationImageService(BadHttp()).collect(news(), source, CollectionContext())
    assert result.failures and result.failures[0].code == "vndb_lookup_failed"

    class BadJson(FakeHttp):
        def post_json(self, url, payload):
            return SimpleNamespace(status_code=200, text="not-json", headers={})
    result = LocalizationImageService(BadJson()).collect(news(), source, CollectionContext())
    assert result.failures


def test_steam_http_error_returns_failure_and_unknown_host_never_dispatches():
    source = steam_source()
    bad = FakeHttp(pages={"default": SimpleNamespace(text="", url=source.url, status_code=503)})
    result = LocalizationImageService(bad).collect(news(localization_context=LocalizationContext(steam_app_ids=[APP])), source, CollectionContext())
    assert result.failures and result.failures[0].code == "steam_page_http_error"
    no_network = FakeHttp()
    result = LocalizationImageService(no_network).collect(news(localization_context=LocalizationContext(steam_app_ids=[APP])),
        SourceRef(url="https://example.com/app/3419820", domain="example.com"), CollectionContext())
    assert not result.candidates and not no_network.gets
