from __future__ import annotations

from galgame_news.discovery.http import SafeHttpClient


def _fixed_public_resolver(_host: str) -> list[str]:
    return ["93.184.216.34"]


def test_safe_http_client_accepts_injected_public_resolver_without_dns():
    class Response:
        status_code = 200
        content = b"ok"
        headers = {"content-type": "text/plain"}
        url = "https://www.youtube.com/watch?v=test"

    client = SafeHttpClient(
        transport=lambda _url, **_kwargs: Response(),
        resolver=_fixed_public_resolver,
    )

    assert client.get("https://www.youtube.com/watch?v=test").status_code == 200
