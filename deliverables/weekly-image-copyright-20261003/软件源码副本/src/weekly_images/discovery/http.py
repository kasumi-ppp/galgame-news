"""Small, injectable HTTP client with SSRF and retry protections."""

from __future__ import annotations

import ipaddress
import re
import socket
import time
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

import httpx


class UnsafeUrlError(ValueError):
    pass


class ResponseTooLarge(ValueError):
    pass


def _verified_www_fallback(url: str, error: Exception) -> str | None:
    message = str(error).casefold()
    if "hostname mismatch" not in message and "wrong_principal" not in message:
        return None
    parts = urlsplit(url)
    host = parts.hostname or ""
    if not host or host.startswith("www."):
        return None
    netloc = f"www.{host}"
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _public_url(
    url: str,
    resolver: Callable[[str], Iterable[str]] | None = None,
) -> None:
    parts = urlsplit(url)
    if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
        raise UnsafeUrlError(f"unsupported URL: {url}")
    host = parts.hostname
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            if resolver is not None:
                addresses = [ipaddress.ip_address(value) for value in resolver(host)]
            else:
                addresses = [
                    ipaddress.ip_address(info[4][0])
                    for info in socket.getaddrinfo(host, None)
                ]
        except (OSError, ValueError):
            # An unresolved public hostname is safe to pass to an injected
            # transport; the real client will report the connection error.
            addresses = []
    for address in addresses:
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise UnsafeUrlError(f"private or non-public URL: {url}")


class SafeHttpClient:
    def __init__(
        self,
        *,
        transport: Callable[..., Any] | None = None,
        timeout: float = 12.0,
        max_retries: int = 3,
        max_response_bytes: int = 5_242_880,
        user_agent: str = "WeeklyImageCollector/2.0",
        resolver: Callable[[str], Iterable[str]] | None = None,
    ):
        self.transport = transport
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.max_response_bytes = max_response_bytes
        self.user_agent = user_agent
        self.resolver = resolver

    def validate_url(self, url: str) -> None:
        """Validate a URL before handing it to any injected or real transport."""
        _public_url(url, self.resolver)

    def get(self, url: str, *, cookies: dict[str, str] | None = None) -> Any:
        current = url
        attempt = 0
        attempt_limit = self.max_retries
        while attempt < attempt_limit:
            attempt += 1
            _public_url(current, self.resolver)
            try:
                request_headers = {"user-agent": self.user_agent}
                cookie_jar = None
                if cookies:
                    for name, value in cookies.items():
                        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", str(name)) or re.search(r"[;\r\n]", str(value)):
                            raise ValueError("invalid cookie name or value")
                if self.transport is not None:
                    if cookies:
                        request_headers["cookie"] = "; ".join(f"{name}={value}" for name, value in cookies.items())
                    response = self.transport(
                        current,
                        timeout=self.timeout,
                        headers=request_headers,
                    )
                else:
                    if cookies:
                        cookie_jar = httpx.Cookies()
                        host = urlsplit(current).hostname
                        for name, value in cookies.items():
                            cookie_jar.set(name, value, domain=host, path="/")
                    response = httpx.get(
                        current,
                        timeout=self.timeout,
                        headers=request_headers,
                        cookies=cookie_jar,
                        follow_redirects=True,
                    )
                final_url = str(getattr(response, "url", current))
                _public_url(final_url, self.resolver)
                content = getattr(response, "content", b"") or b""
                content_length = getattr(response, "headers", {}).get("content-length")
                if content_length and int(content_length) > self.max_response_bytes:
                    raise ResponseTooLarge(
                        f"response exceeds {self.max_response_bytes} bytes"
                    )
                if len(content) > self.max_response_bytes:
                    raise ResponseTooLarge(
                        f"response exceeds {self.max_response_bytes} bytes"
                    )
                status = int(getattr(response, "status_code", 200))
                if status in {408, 429} or status >= 500:
                    if attempt < attempt_limit:
                        time.sleep(min(0.05 * (2 ** (attempt - 1)), 0.2))
                        continue
                return response
            except (UnsafeUrlError, ResponseTooLarge):
                raise
            except Exception as exc:
                fallback = _verified_www_fallback(current, exc)
                if fallback is not None:
                    current = fallback
                    attempt_limit += 1
                    continue
                if attempt >= attempt_limit:
                    raise
                time.sleep(min(0.05 * (2 ** (attempt - 1)), 0.2))
        raise RuntimeError("HTTP retry loop exhausted")
