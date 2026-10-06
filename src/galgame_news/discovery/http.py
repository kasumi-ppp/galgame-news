"""Small, injectable HTTP client with SSRF and retry protections."""

from __future__ import annotations

import ipaddress
import re
import socket
import time
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit

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
        user_agent: str = "WeeklyGalgameImagePrescan/2.0",
        resolver: Callable[[str], Iterable[str]] | None = None,
        trust_env: bool = True,
    ):
        self.transport = transport
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.max_response_bytes = max_response_bytes
        self.user_agent = user_agent
        self.resolver = resolver
        self.trust_env = trust_env

    def validate_url(self, url: str) -> None:
        """Validate a URL before handing it to any injected or real transport."""
        try:
            parts = urlsplit(url)
        except ValueError as exc:
            raise UnsafeUrlError(f"unsupported URL: {url}") from exc
        if parts.username is not None or parts.password is not None:
            raise UnsafeUrlError(f"URL credentials forbidden: {url}")
        _public_url(url, self.resolver)

    def get(self, url: str, *, cookies: dict[str, str] | None = None, max_retries: int | None = None) -> Any:
        current = url
        self.validate_url(url)
        original_host = urlsplit(url).hostname
        cookies = dict(cookies or {})
        for name, value in cookies.items():
            if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", str(name)) or re.search(r"[;\r\n]", str(value)):
                raise ValueError("invalid cookie name or value")
        attempt = 0
        attempt_limit = self.max_retries if max_retries is None else max(1, max_retries)
        while attempt < attempt_limit:
            attempt += 1
            try:
                response = self._redirects(current, cookies, original_host)
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
                if fallback is not None and (max_retries is None or attempt < attempt_limit):
                    current = fallback
                    if max_retries is None:
                        attempt_limit += 1
                    continue
                if attempt >= attempt_limit:
                    raise
                time.sleep(min(0.05 * (2 ** (attempt - 1)), 0.2))
        raise RuntimeError("HTTP retry loop exhausted")

    def _redirects(self, current: str, cookies: dict[str, str], original_host: str | None) -> Any:
        for redirects in range(11):
            self.validate_url(current)
            headers = {"user-agent": self.user_agent}
            if cookies and urlsplit(current).hostname == original_host:
                headers["cookie"] = "; ".join(f"{name}={value}" for name, value in cookies.items())
            response = self._fetch(current, headers)
            final_url = str(getattr(response, "url", current) or current)
            self.validate_url(final_url)
            response_headers = {str(key).lower(): str(value) for key, value in getattr(response, "headers", {}).items()}
            location = response_headers.get("location")
            if int(getattr(response, "status_code", 200)) in {301, 302, 303, 307, 308} and location:
                if redirects == 10:
                    raise UnsafeUrlError("too many HTTP redirects")
                current = urljoin(final_url, location)
                self.validate_url(current)
                continue
            return response
        raise UnsafeUrlError("too many HTTP redirects")

    def _fetch(self, url: str, headers: dict[str, str]) -> Any:
        if self.transport is not None:
            response = self.transport(url, timeout=self.timeout, headers=headers)
            self.validate_url(str(getattr(response, "url", url) or url))
            self._check_length(getattr(response, "headers", {}))
            content = getattr(response, "content", b"") or b""
            if isinstance(content, str):
                content = content.encode()
            if not content and getattr(response, "text", None):
                content = str(response.text).encode()
            self._check_size(len(content))
            return response

        with httpx.stream(
            "GET", url, timeout=self.timeout, headers=headers,
            follow_redirects=False, trust_env=self.trust_env,
        ) as response:
            self.validate_url(str(response.url))
            self._check_length(response.headers)
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                self._check_size(size)
                chunks.append(chunk)
            # Match httpx.Response.read() after collecting through the size cap.
            # Retaining the original response also preserves its decoder state.
            response._content = b"".join(chunks)
            return response

    def _check_length(self, headers: Any) -> None:
        normalized = {str(key).lower(): str(value) for key, value in headers.items()}
        try:
            declared = int(normalized.get("content-length", ""))
        except ValueError:
            return
        self._check_size(declared)

    def _check_size(self, size: int) -> None:
        if size > self.max_response_bytes:
            raise ResponseTooLarge(f"response exceeds {self.max_response_bytes} bytes")
