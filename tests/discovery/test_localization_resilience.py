import json
from types import SimpleNamespace

import pytest

from galgame_news.domain import CollectionContext, NewsItem, SourceRef
from galgame_news.localization.service import LocalizationImageService
from galgame_news.localization.vndb_client import VNDBClient
from galgame_news.pipeline.contracts import CancellationRequested


def response(data=None, status=200, headers=None):
    return SimpleNamespace(status_code=status, headers=headers or {}, text=json.dumps(data or {"results": []}))


def test_vndb_short_429_retries_once_and_long_cooldown_defers():
    class Http:
        calls = 0
        def post_json(self, *_):
            self.calls += 1
            return response(status=429, headers={"retry-after": "0"}) if self.calls == 1 else response({"results": [{"id": "v1"}]})
    http = Http()
    client = VNDBClient(http, min_interval=0)
    assert client.vn("v1", "news")["id"] == "v1"
    assert client.vn("v1", "news")["id"] == "v1"
    assert http.calls == 2
    class Limited(Http):
        def post_json(self, *_):
            self.calls += 1
            return response(status=429, headers={"retry-after": "60"})
    limited = Limited()
    client = VNDBClient(limited, min_interval=0)
    with pytest.raises(RuntimeError, match="deferred"):
        client.vn("v1", "news")
    with pytest.raises(RuntimeError, match="deferred"):
        client.vn("v2", "news")
    assert limited.calls == 1


def test_vndb_query_budget_includes_page_and_skill_never_exits():
    class Http:
        calls = 0
        def post_json(self, *_):
            self.calls += 1
            return response()
    http = Http()
    client = VNDBClient(http, min_interval=0)
    for i in range(15):
        client.vn(f"v{i}", "news")
    client.reserve_page("news")
    with pytest.raises(RuntimeError, match="budget"):
        client.vn("v99", "news")
    assert http.calls == 15
    with pytest.raises(ValueError):
        client.get("stats")
    class Broken:
        def post_json(self, *_):
            raise OSError("network unavailable")
    with pytest.raises(OSError):
        VNDBClient(Broken(), min_interval=0).vn("v1")


def test_vndb_pause_wait_checks_cancellation():
    token = SimpleNamespace(is_cancelled=True, wait_if_paused=lambda: None)
    http = SimpleNamespace(runtime=SimpleNamespace(token=token))
    with pytest.raises(CancellationRequested):
        VNDBClient(http)._wait_seconds(0.1)


def test_conflicting_api_id_rejected_and_failed_requests_count_against_budget():
    class Conflicting:
        def post_json(self, *_):
            return response({"results": [{"id": "v999", "screenshots": []}]})
    with pytest.raises(RuntimeError, match="conflicting"):
        VNDBClient(Conflicting(), min_interval=0).vn("v1", "news")
    class Broken:
        calls = 0
        def post_json(self, *_):
            self.calls += 1
            raise OSError("network unavailable")
    http = Broken()
    client = VNDBClient(http, min_interval=0)
    for i in range(16):
        with pytest.raises(OSError):
            client.vn(f"v{i}", "news")
    with pytest.raises(RuntimeError, match="budget"):
        client.vn("v999", "news")
    assert http.calls == 16


def test_release_fallback_uses_relation_row_and_screenshot_container_only():
    class Http:
        def post_json(self, *_):
            return response(status=503)
        def get(self, url):
            if url.endswith("r1"):
                page = '<a href="/v999">无关作品</a><table><tr><td>Relation</td><td><a href="/v2">本作品</a></td></tr></table>'
            else:
                page = '<a href="https://images.vndb.org/sf/99.jpg">图库外</a><div class="mainbox" id="screenshots"><h2>Screenshots</h2><a href="https://images.vndb.org/sf/2.jpg"><img src="https://images.vndb.org/st/2.jpg"></a></div>'
            return SimpleNamespace(status_code=200, text=page, url=url)
    service = LocalizationImageService(Http())
    item = NewsItem(issue_id="1", sequence=1, section="汉化", title="本作品汉化", body="", source_urls=["https://vndb.org/r1"])
    result = service.collect(item, SourceRef(url=item.source_urls[0], domain="vndb.org"), CollectionContext())
    assert len(result.candidates) == 2
    assert {c.localization_provenance.work_id for c in result.candidates} == {"v2"}
    assert all("99.jpg" not in c.image_url for c in result.candidates)
    assert result.candidates[0].localization_provenance.resolution == "full"
    assert result.failures  # API failures remain visible despite successful fallback.
