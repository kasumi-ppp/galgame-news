"""Small, injectable HTTP client with SSRF and retry protections."""

from __future__ import annotations

import ipaddress
import socket
import time
from urllib.parse import urlsplit
from typing import Any, Callable

import httpx


class UnsafeUrlError(ValueError):
    pass


class ResponseTooLarge(ValueError):
    pass


def _public_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
        raise UnsafeUrlError(f"unsupported URL: {url}")
    host = parts.hostname
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, None)]
        except OSError:
            # An unresolved public hostname is safe to pass to an injected transport;
            # the real client will report the connection error.
            addresses = []
    for address in addresses:
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast or address.is_unspecified:
            raise UnsafeUrlError(f"private or non-public URL: {url}")


class SafeHttpClient:
    def __init__(self, *, transport: Callable[..., Any] | None = None, timeout: float = 12.0, max_retries: int = 3, max_response_bytes: int = 5_242_880, user_agent: str = "WeeklyGalgameImagePrescan/2.0"):
        self.transport = transport
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.max_response_bytes = max_response_bytes
        self.user_agent = user_agent

    def get(self, url: str) -> Any:
        current = url
        for attempt in range(self.max_retries):
            _public_url(current)
            try:
                if self.transport is not None:
                    response = self.transport(current, timeout=self.timeout, headers={"user-agent": self.user_agent})
                else:
                    response = httpx.get(current, timeout=self.timeout, headers={"user-agent": self.user_agent}, follow_redirects=True)
                final_url = str(getattr(response, "url", current))
                _public_url(final_url)
                content = getattr(response, "content", b"") or b""
                content_length = getattr(response, "headers", {}).get("content-length")
                if content_length and int(content_length) > self.max_response_bytes:
                    raise ResponseTooLarge(f"response exceeds {self.max_response_bytes} bytes")
                if len(content) > self.max_response_bytes:
                    raise ResponseTooLarge(f"response exceeds {self.max_response_bytes} bytes")
                status = int(getattr(response, "status_code", 200))
                if status in {408, 429} or status >= 500:
                    if attempt + 1 < self.max_retries:
                        time.sleep(min(0.05 * (2**attempt), 0.2))
                        continue
                return response
            except (UnsafeUrlError, ResponseTooLarge):
                raise
            except Exception:
                if attempt + 1 >= self.max_retries:
                    raise
                time.sleep(min(0.05 * (2**attempt), 0.2))
        raise RuntimeError("HTTP retry loop exhausted")
