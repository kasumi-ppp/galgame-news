"""X/Twitter source adapter with injectable public/API transports."""

from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ...domain import CollectionResult, FailureRecord, FailureStage, ReviewReason
from ...video.discovery import collect_entity_videos, discover_html_video_urls, explicit_x_html_entity_urls, video_candidate
from ..http import SafeHttpClient
from .common import _candidate, _is_public_x_media, _upgrade_image_url


class XAdapter:
    def __init__(self, *, token: str | None = None, transport: Callable[..., Any] | None = None, public_transport: Callable[..., Any] | None = None, public_resolver: Callable[[str], Any] | None = None, public_timeout: float = 4.0, socialdata_mode: bool = False):
        self.token = token
        self.transport = transport
        self.socialdata_mode = socialdata_mode
        self.public_transport = public_transport
        self.public_resolver = public_resolver
        self.public_client = SafeHttpClient(
            transport=public_transport,
            timeout=min(max(float(public_timeout), 1.0), 8.0),
            max_retries=1,
            resolver=public_resolver,
        )
        # API transport and public-page transport are injectable, but all page
        # requests still pass through SafeHttpClient's URL/redirect checks.
        self.entity_client = SafeHttpClient(
            transport=public_transport or transport,
            timeout=min(max(float(public_timeout), 1.0), 8.0),
            max_retries=1,
            resolver=public_resolver,
        )

    @staticmethod
    def _as_list(value: Any) -> list[Any]:
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        if isinstance(value, dict):
            return [value]
        return []

    @staticmethod
    def _photo_urls(media_items: list[Any]) -> list[str]:
        urls: list[str] = []
        for item in media_items:
            if isinstance(item, str):
                urls.append(item)
                continue
            if not isinstance(item, dict):
                continue
            media_type = str(item.get("type") or item.get("media_type") or "").casefold()
            if media_type and media_type not in {"photo", "image"}:
                continue
            for key in ("media_url_https", "media_url", "image_url", "url"):
                value = item.get(key)
                if isinstance(value, str):
                    urls.append(value)
        return urls

    @classmethod
    def _api_image_urls(cls, payload: Any) -> list[str]:
        """Read photo attachments from common X/TwitterAPI.io tweet shapes."""

        if not isinstance(payload, dict):
            return []
        posts: list[dict[str, Any]] = []
        for key in ("tweet", "data"):
            value = payload.get(key)
            if isinstance(value, dict):
                posts.append(value)
        posts.extend(item for item in cls._as_list(payload.get("tweets")) if isinstance(item, dict))
        if not posts and any(key in payload for key in ("entities", "extendedEntities", "extended_entities", "attachments", "media")):
            posts.append(payload)

        urls: list[str] = []
        for post in posts:
            urls.extend(value for value in cls._as_list(post.get("image_urls")) if isinstance(value, str))
            for key in ("media", "photos", "images"):
                urls.extend(cls._photo_urls(cls._as_list(post.get(key))))
            for container_key in ("entities", "extended_entities", "extendedEntities"):
                container = post.get(container_key)
                if isinstance(container, dict):
                    urls.extend(cls._photo_urls(cls._as_list(container.get("media"))))
            attachments = post.get("attachments")
            if isinstance(attachments, dict):
                urls.extend(cls._photo_urls(cls._as_list(attachments.get("media"))))
                media_keys = {str(key) for key in cls._as_list(attachments.get("media_keys"))}
                includes = payload.get("includes")
                included_media = cls._as_list(includes.get("media")) if isinstance(includes, dict) else []
                if media_keys:
                    urls.extend(cls._photo_urls([
                        item for item in included_media
                        if isinstance(item, dict) and str(item.get("media_key") or item.get("key") or "") in media_keys
                    ]))

        accepted: list[str] = []
        seen: set[str] = set()
        for value in urls:
            upgraded = _upgrade_image_url(value)
            if upgraded and _is_public_x_media(upgraded) and upgraded not in seen:
                seen.add(upgraded)
                accepted.append(upgraded)
        return accepted

    @classmethod
    def _api_post_text(cls, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        for key in ("tweet", "data"):
            value = payload.get(key)
            if isinstance(value, dict):
                text = value.get("full_text") or value.get("text")
                if isinstance(text, str):
                    return text.strip()
        for value in cls._as_list(payload.get("tweets")):
            if isinstance(value, dict):
                text = value.get("full_text") or value.get("text")
                if isinstance(text, str):
                    return text.strip()
        text = payload.get("full_text") or payload.get("text")
        if isinstance(text, str):
            return text.strip()
        return ""

    @classmethod
    def _api_image_alt_text(cls, payload: Any, image_url: str) -> str | None:
        target = _upgrade_image_url(image_url)
        found: list[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                media_type = str(value.get("type") or value.get("media_type") or "").casefold()
                alt = value.get("alt_text") or value.get("alt")
                media_urls = [value.get(key) for key in ("media_url_https", "media_url", "image_url", "url")]
                if media_type in {"photo", "image"} and isinstance(alt, str) and any(
                    isinstance(url, str) and _upgrade_image_url(url) == target for url in media_urls
                ):
                    found.append(alt.strip())
                for child in value.values():
                    visit(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    visit(child)

        visit(payload)
        return " ".join(dict.fromkeys(value for value in found if value)) or None

    @classmethod
    def _api_image_metadata(cls, payload: Any, image_url: str) -> tuple[str | None, int | None, int | None]:
        """Match the normalized photo to its raw API URL and original dimensions."""
        target = _upgrade_image_url(image_url)
        raw_url: str | None = None
        width: int | None = None
        height: int | None = None

        def dimension(value: Any) -> int | None:
            return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None

        def visit(value: Any) -> None:
            nonlocal raw_url, width, height
            if isinstance(value, dict):
                media_type = str(value.get("type") or value.get("media_type") or "").casefold()
                if not media_type or media_type in {"photo", "image"}:
                    for key in ("media_url_https", "media_url", "image_url", "url"):
                        url = value.get(key)
                        if isinstance(url, str) and _upgrade_image_url(url) == target:
                            raw_url = raw_url or url
                            info = value.get("original_info")
                            if isinstance(info, dict):
                                width = width or dimension(info.get("width"))
                                height = height or dimension(info.get("height"))
                for child in value.values():
                    visit(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    visit(child)
            elif isinstance(value, str) and raw_url is None and _is_public_x_media(value) and _upgrade_image_url(value) == target:
                raw_url = value

        visit(payload)
        return raw_url, width, height

    @classmethod
    def _api_video_urls(cls, payload: Any) -> list[str]:
        if not isinstance(payload, dict):
            return []
        posts: list[dict[str, Any]] = []
        for key in ("tweet", "data"):
            value = payload.get(key)
            if isinstance(value, dict):
                posts.append(value)
        posts.extend(item for item in cls._as_list(payload.get("tweets")) if isinstance(item, dict))
        if not posts and any(key in payload for key in ("entities", "extendedEntities", "extended_entities", "attachments", "media")):
            posts.append(payload)
        selected: list[str] = []
        for post in posts:
            media_items = []
            for key in ("entities", "extended_entities", "extendedEntities"):
                container = post.get(key)
                if isinstance(container, dict):
                    media_items.extend(cls._as_list(container.get("media")))
            attachments = post.get("attachments")
            if isinstance(attachments, dict):
                media_items.extend(cls._as_list(attachments.get("media")))
            for key in ("media", "photos", "images"):
                media_items.extend(cls._as_list(post.get(key)))
            for item in media_items:
                if not isinstance(item, dict):
                    continue
                media_type = str(item.get("type") or item.get("media_type") or "").casefold()
                video_info = item.get("video_info") or item.get("videoInfo") or {}
                if media_type not in {"video", "animated_gif"} or not isinstance(video_info, dict):
                    continue
                variants = cls._as_list(video_info.get("variants"))
                mp4 = [variant for variant in variants if isinstance(variant, dict)
                       and str(variant.get("content_type") or variant.get("contentType") or "").casefold() == "video/mp4"
                       and isinstance(variant.get("url"), str)
                       and (urlsplit(variant["url"]).hostname or "").casefold() == "video.twimg.com"
                       and urlsplit(variant["url"]).path.casefold().endswith(".mp4")]
                if mp4:
                    best = max(mp4, key=lambda value: int(value.get("bitrate") or 0))
                    selected.append(best["url"])
                    continue
                hls = [variant for variant in variants if isinstance(variant, dict)
                       and str(variant.get("content_type") or variant.get("contentType") or "").casefold() in {"application/x-mpegurl", "application/vnd.apple.mpegurl"}
                       and isinstance(variant.get("url"), str)
                       and (urlsplit(variant["url"]).hostname or "").casefold() == "video.twimg.com"
                       and urlsplit(variant["url"]).path.casefold().endswith(".m3u8")]
                if hls:
                    selected.append(hls[0]["url"])
        return list(dict.fromkeys(selected))

    def collect(self, news_item, source_ref, context) -> CollectionResult:
        source_ref.requires_review = True
        source_ref.review_reasons = list(dict.fromkeys([*source_ref.review_reasons, ReviewReason.X_SOURCE]))
        failures: list[FailureRecord] = []
        api_data: dict[str, Any] = {}
        if self.socialdata_mode and not self.token:
            failures.append(FailureRecord(
                stage=FailureStage.COLLECT, news_id=news_item.id,
                code="socialdata_key_missing", message="SocialData was enabled but no API key is configured",
                source_url=source_ref.url, retryable=False,
            ))
        if self.token and self.transport is not None:
            try:
                response = self.transport(source_ref.url, token=self.token, timeout=context.timeout_seconds)
                api_data = response if isinstance(response, dict) else getattr(response, "json", lambda: {})()
                urls = self._api_image_urls(api_data)
                api_video_failures: list[FailureRecord] = []
                api_video_candidates = self._api_video_candidates(news_item, source_ref, api_data, api_video_failures)
                if urls or api_video_candidates:
                    candidates = [_candidate(news_item, url, source_ref, context) for url in urls]
                    for candidate in candidates:
                        (candidate.media_source_url, candidate.expected_width,
                         candidate.expected_height) = self._api_image_metadata(api_data, candidate.image_url)
                        candidate.signals["x_media_extraction"] = "structured_post_media"
                        candidate.signals["x_api_photo"] = True
                        if self.socialdata_mode:
                            candidate.signals["socialdata_photo"] = True
                        post_text = self._api_post_text(api_data)
                        if post_text:
                            candidate.nearby_text = post_text
                            candidate.signals["tweet_text"] = post_text
                            candidate.signals["page_title"] = post_text
                        candidate.image_alt = self._api_image_alt_text(api_data, candidate.image_url)
                        if candidate.image_alt:
                            candidate.signals["alt"] = candidate.image_alt
                    return CollectionResult(
                        candidates=candidates[:context.max_candidates],
                        video_candidates=api_video_candidates,
                        failures=api_video_failures,
                        manual_review_reasons=[ReviewReason.X_SOURCE],
                    )
            except Exception as exc:
                failures.append(FailureRecord(
                    stage=FailureStage.COLLECT,
                    news_id=news_item.id,
                    code="x_api_error",
                    message=str(exc),
                    source_url=source_ref.url,
                    retryable=True,
                ))

        public_result = self._collect_public_html(news_item, source_ref, context, api_data)
        public_result.failures[:0] = failures
        if failures and ReviewReason.NETWORK_RESTRICTED not in public_result.manual_review_reasons:
            public_result.manual_review_reasons.append(ReviewReason.NETWORK_RESTRICTED)
        return public_result

    def _collect_public_html(self, news_item, source_ref, context, api_data) -> CollectionResult:
        try:
            response = self.public_client.get(source_ref.url)
            html = getattr(response, "text", "") or ""
            page_url = str(getattr(response, "url", source_ref.url) or source_ref.url)
            soup = BeautifulSoup(html, "html.parser")
            urls = []
            for tag in soup.find_all("meta"):
                key = (tag.get("property") or tag.get("name") or "").casefold()
                if key in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"} and tag.get("content"):
                    image_url = _upgrade_image_url(urljoin(page_url, tag["content"]))
                    if image_url and _is_public_x_media(image_url):
                        urls.append(image_url)

            # The structured post response can include the short URL/entity
            # data needed for the existing video metadata path.
            video_urls = discover_html_video_urls(
                html,
                page_url,
                include_anchor_links=False,
            )
            links = [
                {"expanded_url": url}
                for url in explicit_x_html_entity_urls(html, page_url)
            ]
            links.extend({"expanded_url": url} for url in video_urls)
            entity_failures: list[FailureRecord] = []

            def fetch_html(url: str) -> tuple[str, str]:
                child = self.public_client.get(url)
                final_url = str(getattr(child, "url", url) or url)
                return final_url, getattr(child, "text", "") or ""

            html_videos = collect_entity_videos(
                news_item, source_ref, {"entities": {"urls": links}},
                fetch_html=fetch_html,
                url_validator=self.public_client.validate_url,
                failures=entity_failures,
            )
            api_videos = self._api_video_candidates(news_item, source_ref, api_data, entity_failures) if api_data else []
            entity_videos = list({
                candidate.video_url: candidate
                for candidate in [*api_videos, *html_videos]
            }.values())
            candidates = [_candidate(news_item, url, source_ref, context) for url in dict.fromkeys(urls)]
            for candidate in candidates:
                candidate.signals["x_media_extraction"] = "public_page_metadata"
            return CollectionResult(
                candidates=candidates[:context.max_candidates],
                video_candidates=entity_videos,
                failures=entity_failures,
                manual_review_reasons=[ReviewReason.X_SOURCE],
            )
        except Exception as exc:
            return CollectionResult(
                failures=[FailureRecord(
                    stage=FailureStage.COLLECT,
                    news_id=news_item.id,
                    code="x_public_metadata_error",
                    message=str(exc),
                    source_url=source_ref.url,
                    retryable=True,
                )],
                manual_review_reasons=[ReviewReason.X_SOURCE, ReviewReason.NETWORK_RESTRICTED],
            )

    def _entity_videos(self, news_item, source_ref, data, failures=None) -> list:
        if not isinstance(data, dict):
            return []

        def fetch_html(url: str) -> tuple[str, str]:
            child = self.entity_client.get(url)
            final_url = str(getattr(child, "url", url) or url)
            return final_url, getattr(child, "text", "") or ""

        return collect_entity_videos(
            news_item,
            source_ref,
            data,
            fetch_html=fetch_html,
            url_validator=self.entity_client.validate_url,
            failures=failures,
        )

    def _api_video_candidates(self, news_item, source_ref, data, failures=None) -> list:
        values = [] if self.socialdata_mode else self._entity_videos(news_item, source_ref, data, failures)
        seen = {candidate.video_url for candidate in values}
        for url in self._api_video_urls(data):
            try:
                self.entity_client.validate_url(url)
            except Exception:
                continue
            candidate = video_candidate(news_item, url, source_ref, title=self._api_post_text(data))
            if candidate is not None and candidate.video_url not in seen:
                seen.add(candidate.video_url)
                values.append(candidate)
        return values
