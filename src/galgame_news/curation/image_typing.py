"""Deterministic image typing and news-specific image acceptance policy."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit

from ..config import ImageTypeConfig
from ..domain import EventType, ImageCandidate, ImageNeed, ImageType, NewsItem


@dataclass(frozen=True)
class ImageTypeResult:
    image_type: ImageType
    confidence: float
    supporting_signals: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImageTypeDecision:
    accepted: bool
    fallback_only: bool = False
    rejection_reason: str | None = None
    requires_review: bool = False
    type_match: float = 0.0


class ImageRequirement(str, Enum):
    CG = "cg"
    ANNOUNCEMENT = "announcement"
    GOODS = "goods"
    RELEASE = "release"
    GENERIC = "generic"
    UNKNOWN = "unknown"


_LOGO_TERMS = ("logo", "brand-logo", "site-logo", "logo_mark")
_BANNER_TERMS = ("banner", "bnr", "header", "footer", "mainvisual-banner", "campaign-banner")
_UI_TERMS = ("button", "btn", "icon", "arrow", "menu", "navigation", "loading", "spinner", "favicon", "steam_share_image")
_GOODS_TERMS = (
    "goods", "shop", "store", "product", "item", "tokuten", "tapestry", "acrylic", "badge", "standee", "merchandise",
    "stand", "特典", "商品", "周边", "周邊",
)
_COVER_TERMS = ("cover", "package", "jacket", "box", "pkg", "capsule")
_KEY_VISUAL_TERMS = ("keyvisual", "key_visual", "mainvisual", "main_visual", "kv", "visual")
_CHARACTER_TERMS = ("character", "chara", "standing", "sprite", "tachie", "立绘", "立繪")
_SCREENSHOT_TERMS = ("screenshot", "screenshots", "screen", "sample", "play", "ingame", "in_game")
_PHOTO_TERMS = ("photo", "photograph", "realphoto", "cosplay", "照片", "写真", "实拍", "實拍")
_ANNOUNCEMENT_TERMS = ("announcement", "commemorative", "celebration", "anniversary", "贺图", "賀圖", "纪念图", "紀念圖", "应援图", "應援圖", "倒计时图", "倒計時圖")
_TYPE_MATCH_SCORES = {
    ImageRequirement.CG: {ImageType.GAME_CG: 1.0, ImageType.GAMEPLAY_SCREENSHOT: 0.8, ImageType.KEY_VISUAL: 0.4, ImageType.ANNOUNCEMENT_ART: 0.25, ImageType.UNKNOWN: 0.1},
    ImageRequirement.ANNOUNCEMENT: {ImageType.ANNOUNCEMENT_ART: 1.0, ImageType.KEY_VISUAL: 0.8, ImageType.GAME_CG: 0.35, ImageType.GAMEPLAY_SCREENSHOT: 0.25, ImageType.UNKNOWN: 0.1},
    ImageRequirement.GOODS: {ImageType.GOODS: 1.0, ImageType.ANNOUNCEMENT_ART: 0.7, ImageType.KEY_VISUAL: 0.5, ImageType.GAME_CG: 0.25, ImageType.GAMEPLAY_SCREENSHOT: 0.2, ImageType.UNKNOWN: 0.1},
    ImageRequirement.RELEASE: {ImageType.GAME_CG: 1.0, ImageType.GAMEPLAY_SCREENSHOT: 0.85, ImageType.KEY_VISUAL: 0.65, ImageType.COVER: 0.55, ImageType.UNKNOWN: 0.1},
    ImageRequirement.GENERIC: {ImageType.GAME_CG: 0.9, ImageType.GAMEPLAY_SCREENSHOT: 0.8, ImageType.ANNOUNCEMENT_ART: 0.7, ImageType.KEY_VISUAL: 0.6, ImageType.UNKNOWN: 0.1},
    ImageRequirement.UNKNOWN: {ImageType.GAME_CG: 0.9, ImageType.GAMEPLAY_SCREENSHOT: 0.8, ImageType.ANNOUNCEMENT_ART: 0.7, ImageType.KEY_VISUAL: 0.6, ImageType.UNKNOWN: 0.1},
}


def _norm(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value)).casefold()


def _has_term(text: str, term: str) -> bool:
    term = _norm(term)
    if re.fullmatch(r"[a-z0-9_-]+", term):
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None
    return term in text


def _any_term(text: str, terms: tuple[str, ...]) -> str | None:
    for term in terms:
        if _has_term(text, term):
            return term
    return None


def _signal_text(candidate: ImageCandidate) -> str:
    values: list[str] = []
    for url in (candidate.image_url, candidate.source_url):
        parts = urlsplit(url)
        values.extend((parts.path, parts.query))
    for key, value in candidate.signals.items():
        values.append(str(key))
        if isinstance(value, str):
            values.append(value)
    return _norm(" ".join(values))


def _news_text(news: NewsItem) -> str:
    return _norm(" ".join([news.title, news.body, *news.keywords]))


def _aspect(candidate: ImageCandidate) -> float | None:
    if not candidate.width or not candidate.height:
        return None
    return candidate.width / candidate.height


class ImageTypeClassifier:
    def __init__(self, config: ImageTypeConfig | None = None):
        self.config = config or ImageTypeConfig()

    def _result(self, image_type: ImageType, confidence: float, signals: tuple[str, ...]) -> ImageTypeResult:
        if confidence < self.config.minimum_type_confidence:
            return ImageTypeResult(ImageType.UNKNOWN, 0.0, ("below_minimum_confidence",))
        return ImageTypeResult(image_type, min(1.0, max(0.0, confidence)), signals)

    def _game_cg_result(self, gallery: bool, explicit_cg: bool, similar_gallery: bool, horizontal_scene: bool) -> ImageTypeResult:
        signals = ["gallery_page" if gallery else "cg_filename"]
        if explicit_cg:
            signals.append("explicit_cg")
        if similar_gallery:
            signals.append("similar_gallery_images")
        if horizontal_scene:
            signals.append("horizontal_scene")
        return self._result(ImageType.GAME_CG, 0.88 + 0.04 * len(signals), tuple(signals))

    def classify(self, news: NewsItem, candidate: ImageCandidate) -> ImageTypeResult:
        text = _signal_text(candidate)
        news_text = _news_text(news)
        aspect = _aspect(candidate)

        # Explicit UI assets must win even when they come from an official page.
        if _has_term(text, "steam_share_image"):
            return self._result(ImageType.UI, 1.0, ("steam_share_image",))
        ui_term = _any_term(text, _UI_TERMS)
        if ui_term:
            return self._result(ImageType.UI, 0.98, (f"ui:{ui_term}",))

        logo_term = _any_term(text, _LOGO_TERMS)
        if logo_term:
            return self._result(ImageType.LOGO, 0.98, (f"logo:{logo_term}",))

        banner_term = _any_term(text, _BANNER_TERMS)
        extreme_aspect = aspect is not None and (aspect >= self.config.banner_aspect_ratio or aspect <= 1 / self.config.banner_aspect_ratio)
        explicit_non_banner_semantics = bool(_any_term(text, _GOODS_TERMS + _COVER_TERMS + _CHARACTER_TERMS + _KEY_VISUAL_TERMS + _SCREENSHOT_TERMS + _PHOTO_TERMS + _ANNOUNCEMENT_TERMS)) or bool(re.search(r"gallery|ギャラリー|ギャラリ|(?:^|[/_.-])(?:cg|ev|event|scene)(?:[_-]?\d+|cg)?(?:[/_.-]|$)", text))
        if banner_term or (extreme_aspect and not explicit_non_banner_semantics):
            signals: list[str] = []
            if banner_term:
                signals.append(f"banner:{banner_term}")
            if extreme_aspect:
                signals.append(f"banner_aspect:{aspect:.3f}")
            return self._result(ImageType.BANNER, 0.94 + 0.04 * bool(extreme_aspect), tuple(signals))

        steam_screenshot = candidate.source_type.value == "steam" and bool(re.search(r"screenshots?|(?:^|[/_.-])ss(?:[_-]?\d+)?(?:[/_.-]|$)", text))
        if steam_screenshot:
            return self._result(ImageType.GAMEPLAY_SCREENSHOT, 0.93, ("steam_screenshot",))

        goods_terms = tuple(term for term in _GOODS_TERMS if not (candidate.source_type.value == "steam" and term in {"store", "item"}))
        goods_term = _any_term(text, goods_terms)
        if goods_term:
            return self._result(ImageType.GOODS, 0.96, (f"goods:{goods_term}",))

        cover_term = _any_term(text, _COVER_TERMS)
        if cover_term:
            signals = [f"cover:{cover_term}"]
            if aspect is not None and aspect < 1 / self.config.character_aspect_ratio:
                signals.append(f"cover_portrait:{aspect:.3f}")
            return self._result(ImageType.COVER, 0.91 + 0.05 * (len(signals) - 1), tuple(signals))

        announcement_term = _any_term(text, _ANNOUNCEMENT_TERMS)
        gift_news = bool(re.search(r"贺图|賀圖|纪念图|紀念圖|应援图|應援圖|倒计时图|倒計時圖|周年|週年|anniversary|commemorative|celebration", news_text))
        if announcement_term and (gift_news or "announcement" in text):
            return self._result(ImageType.ANNOUNCEMENT_ART, 0.94, (f"announcement:{announcement_term}", "gift_news"))

        gallery = bool(re.search(r"gallery|ギャラリー|ギャラリ", text)) or bool(candidate.signals.get("gallery"))
        cg_filename = bool(re.search(r"(?:^|[/_.-])(?:cg|ev|event|scene)(?:[_-]?\d+|cg)?(?:[/_.-]|$)", text))
        explicit_cg = bool(candidate.signals.get("cg_match")) or bool(re.search(r"event.?cg|イベントcg", text))
        similar_gallery = any(
            isinstance(candidate.signals.get(key), (int, float)) and float(candidate.signals[key]) >= self.config.minimum_gallery_group_size
            for key in ("gallery_image_count", "gallery_group_size", "similar_gallery_images")
        )
        horizontal_scene = aspect is not None and aspect >= self.config.scene_aspect_ratio
        is_game_cg = (gallery and (cg_filename or explicit_cg or similar_gallery or horizontal_scene)) or (cg_filename and not _any_term(text, _LOGO_TERMS + _BANNER_TERMS + _GOODS_TERMS))
        if is_game_cg and not _any_term(text, _CHARACTER_TERMS + _KEY_VISUAL_TERMS):
            return self._game_cg_result(gallery, explicit_cg, similar_gallery, horizontal_scene)

        character_term = _any_term(text, _CHARACTER_TERMS)
        if character_term:
            signals = [f"character:{character_term}"]
            if aspect is not None and aspect <= 1 / self.config.character_aspect_ratio:
                signals.append(f"character_aspect:{aspect:.3f}")
            if candidate.signals.get("transparent") is True:
                signals.append("transparent_background")
            return self._result(ImageType.CHARACTER_ART, 0.9 + 0.03 * (len(signals) - 1), tuple(signals))

        key_visual_term = _any_term(text, _KEY_VISUAL_TERMS)
        if key_visual_term:
            return self._result(ImageType.KEY_VISUAL, 0.92, (f"key_visual:{key_visual_term}",))

        if is_game_cg:
            return self._game_cg_result(gallery, explicit_cg, similar_gallery, horizontal_scene)

        screenshot_term = _any_term(text, _SCREENSHOT_TERMS)
        if steam_screenshot or screenshot_term:
            return self._result(ImageType.GAMEPLAY_SCREENSHOT, 0.93, ("steam_screenshot" if steam_screenshot else f"screenshot:{screenshot_term}",))

        photo_term = _any_term(text, _PHOTO_TERMS)
        if photo_term:
            return self._result(ImageType.PHOTO, 0.9, (f"photo:{photo_term}",))

        # Anime-like names, officiality, and aspect ratio alone are not enough.
        return ImageTypeResult(ImageType.UNKNOWN, 0.0, ())


class ImageRequirementPolicy:
    def __init__(self, config: ImageTypeConfig | None = None):
        self.config = config or ImageTypeConfig()

    @staticmethod
    def _requirement(news: NewsItem) -> ImageRequirement:
        text = _news_text(news)
        if re.search(r"贺图|賀圖|纪念图|紀念圖|应援图|應援圖|倒计时图|倒計時圖|周年|週年|commemorative|celebration|anniversary", text):
            return ImageRequirement.ANNOUNCEMENT
        if news.event_type is EventType.GOODS or re.search(r"周边|周邊|商品|グッズ|goods|shop|merchandise", text, re.I):
            return ImageRequirement.GOODS
        if re.search(r"CG\s*(?:公开|公開|更新)|新\s*CG|事件\s*CG|イベント\s*CG|画面公开|畫面公開|游戏截图公开|遊戲截圖公開|gallery\s*更新|ギャラリー更新", text, re.I):
            return ImageRequirement.CG
        if news.event_type is EventType.RELEASE or re.search(r"发售|発売|预约|預約|登陆steam|登录steam|上架steam|steam上线|steam上線", text, re.I):
            return ImageRequirement.RELEASE
        if news.image_need is ImageNeed.EXPLICIT_NEW_IMAGE:
            return ImageRequirement.GENERIC
        return ImageRequirement.UNKNOWN

    def evaluate(self, news: NewsItem, result: ImageTypeResult) -> ImageTypeDecision:
        requirement = self._requirement(news)
        rejected = set(self.config.rejected_image_types_by_requirement.get(requirement.value, ()))
        if result.image_type.value in rejected:
            return ImageTypeDecision(False, rejection_reason=f"{result.image_type.value} is not suitable for {requirement} image news", type_match=0.0)
        type_match = self.type_match(news, result.image_type)
        fallback_only = requirement is ImageRequirement.CG and result.image_type is ImageType.KEY_VISUAL
        requires_review = result.image_type is ImageType.UNKNOWN and self.config.unknown_requires_review
        return ImageTypeDecision(True, fallback_only=fallback_only, requires_review=requires_review, type_match=type_match)

    def type_match(self, news: NewsItem, image_type: ImageType) -> float:
        return _TYPE_MATCH_SCORES[self._requirement(news)].get(image_type, 0.25)

    def is_fallback_only(self, news: NewsItem, image_type: ImageType) -> bool:
        return self._requirement(news) is ImageRequirement.CG and image_type is ImageType.KEY_VISUAL
