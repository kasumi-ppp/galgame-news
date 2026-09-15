"""Conservative game/entity matching for image candidates.

The matcher intentionally returns ``None`` when the page does not contain
enough context.  Callers can retain such candidates for editorial review
without risking that a random CDN image is selected automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from urllib.parse import unquote, urlsplit

from ..domain import ImageCandidate, NewsItem, SourceType


@dataclass(frozen=True)
class EntityMatchResult:
    matched: bool | None
    confidence: float
    matched_entities: tuple[str, ...] = ()
    conflicting_entities: tuple[str, ...] = ()
    supporting_signals: tuple[str, ...] = ()
    official_domain_match: bool = False


def _compact(value: object) -> str:
    """Normalize Unicode and remove spacing/decorative punctuation.

    Keeping only letters and numbers makes full/half-width, quote and bracket
    variants compare identically while preserving digits and subtitle words.
    """

    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return "".join(char for char in text if unicodedata.category(char)[0] in {"L", "N"})


def _domain(value: str) -> str:
    return (urlsplit(value).hostname or "").casefold().rstrip(".")


def _app_ids(values: list[str]) -> set[str]:
    found: set[str] = set()
    for value in values:
        found.update(re.findall(r"(?:/app/|/steam/apps/|steam_app_id[=:])([0-9]{3,})", value, re.I))
    return found


class EntityMatcher:
    """Match a candidate to a news item's game using page-local evidence."""

    _TEXT_SIGNAL_KEYS = {
        "page_title", "title", "alt", "nearby_text", "caption", "description",
        "game_slug", "entity", "game_name", "brand", "original_url",
    }
    _NUMERIC_EVIDENCE = ("game_match", "organization_match", "page_match", "event_match", "character_match", "cg_match")

    def match(self, news: NewsItem, candidate: ImageCandidate) -> EntityMatchResult:
        aliases = [str(name) for name in news.game_names if str(name).strip()]
        # A title often contains the canonical game name even when an older
        # parser did not populate game_names.
        title_aliases = re.findall(r"《([^》]+)》|\[([^\]]+)\]", news.title)
        aliases.extend(next((part for part in pair if part), "") for pair in title_aliases)
        aliases = list(dict.fromkeys(name for name in aliases if name))

        url_values = [candidate.image_url, candidate.source_url]
        text_values: list[str] = list(url_values)
        page_values: list[str] = []
        for key, value in candidate.signals.items():
            if key in self._TEXT_SIGNAL_KEYS:
                text_values.append(str(value))
                page_values.append(str(value))
        evidence = " ".join(unquote(value) for value in text_values)
        compact_evidence = _compact(evidence)
        page_text = _compact(" ".join(page_values))

        # Explicit parser evidence is stronger than a filename heuristic.
        supporting: list[str] = []
        for key in self._NUMERIC_EVIDENCE:
            value = candidate.signals.get(key)
            if isinstance(value, (int, float)) and float(value) >= 0.8:
                supporting.append(key)

        news_domains = {_domain(url) for url in news.source_urls if _domain(url)}
        candidate_domain = _domain(candidate.source_url)
        official_domain_match = bool(candidate_domain and any(
            candidate_domain == domain or candidate_domain.endswith("." + domain) or domain.endswith("." + candidate_domain)
            for domain in news_domains
        ))
        if official_domain_match:
            supporting.append("official_domain_match")

        # Steam IDs are an unambiguous conflict signal.
        news_ids = _app_ids(list(news.source_urls) + [str(value) for value in news.keywords])
        candidate_ids = _app_ids(url_values + [f"{key}={value}" for key, value in candidate.signals.items()])
        if news_ids and candidate_ids and news_ids.isdisjoint(candidate_ids):
            return EntityMatchResult(False, 1.0, conflicting_entities=tuple(sorted(candidate_ids)), supporting_signals=("steam_app_id_mismatch",), official_domain_match=official_domain_match)
        if news_ids and candidate_ids and not news_ids.isdisjoint(candidate_ids):
            return EntityMatchResult(True, 0.98, supporting_signals=("steam_app_id_match",), official_domain_match=official_domain_match)

        conflicts: list[str] = []
        for key in ("entity_conflict", "conflicting_entity", "other_game_name", "other_title"):
            value = candidate.signals.get(key)
            if value not in (None, False, "", 0):
                conflicts.append(str(value) if value is not True else key)
        if candidate.signals.get("brand_match") is False:
            conflicts.append("brand_mismatch")
        host_text = _compact(candidate_domain)
        # A branded domain containing a distinctive numbered title is strong
        # evidence of a different work when the target entity is absent.
        target_text = " ".join(_compact(alias) for alias in aliases)
        if "9nineproject" in host_text and "9nine" not in target_text:
            conflicts.append("9-nine")
        # A third-party page title that clearly names another work is a
        # contradiction, not merely missing evidence.  Generic words such as
        # "CG" or "image" are deliberately ignored.
        if page_values:
            page_title = page_values[0]
            title_tokens = re.findall(r"[A-Z][A-Za-z0-9]{2,}|\d[\w-]{2,}|[\u3040-\u30ff\u4e00-\u9fff]{2,}", page_title)
            if title_tokens and not any(_compact(alias) in page_text for alias in aliases):
                generic = {"CG", "GAME", "IMAGE", "SCREENSHOT", "OFFICIAL", "GALLERY", "公式", "公式サイト", "官方网站", "画像", "画像一覧", "画像公開", "ギャラリー", "ゲーム", "作品", "トップ", "ニュース", "新着"}
                other = [token for token in title_tokens if token.casefold() not in {word.casefold() for word in generic}]
                if other:
                    conflicts.extend(other)
        if conflicts:
            return EntityMatchResult(False, 1.0, conflicting_entities=tuple(dict.fromkeys(conflicts)), supporting_signals=tuple(dict.fromkeys(supporting)), official_domain_match=official_domain_match)

        matched: list[str] = []
        for alias in aliases:
            normalized = _compact(alias)
            if not normalized:
                continue
            if normalized in compact_evidence:
                # Very short names are common words and need page/brand or
                # official-domain context before they can match.
                is_short = (normalized.isascii() and len(normalized) <= 3) or (not normalized.isascii() and len(normalized) <= 2)
                contextual = official_domain_match or bool(page_text and normalized in page_text) or bool(supporting)
                if not is_short or contextual:
                    matched.append(alias)

        if matched:
            supporting.append("entity_name_in_context")
            confidence = 0.95 if official_domain_match else 0.86
            if page_text and any(_compact(alias) in page_text for alias in matched):
                supporting.append("page_context")
                confidence = min(0.98, confidence + 0.04)
            return EntityMatchResult(True, confidence, tuple(matched), supporting_signals=tuple(dict.fromkeys(supporting)), official_domain_match=official_domain_match)

        if any(key in supporting for key in ("game_match", "organization_match", "page_match", "event_match")):
            return EntityMatchResult(True, 0.9, supporting_signals=tuple(dict.fromkeys([*supporting, "explicit_entity_signal"])), official_domain_match=official_domain_match)
        if candidate.source_type is SourceType.OFFICIAL_SITE and supporting and set(supporting).issubset({"cg_match"}):
            return EntityMatchResult(True, 0.86, supporting_signals=tuple(dict.fromkeys([*supporting, "official_cg_signal"])), official_domain_match=official_domain_match)

        if official_domain_match and candidate.source_type in {SourceType.OFFICIAL_SITE, SourceType.OFFICIAL_X, SourceType.STEAM}:
            return EntityMatchResult(True, 0.9, supporting_signals=tuple(dict.fromkeys([*supporting, "official_source"])), official_domain_match=True)
        # Legacy resolver results may identify an official game page without
        # copying the original document URL into NewsItem.  Keep that source
        # usable, but expose the weak evidence so callers can lower priority.
        if candidate.source_type is SourceType.OFFICIAL_SITE and candidate_domain and not supporting:
            compact_aliases = [_compact(alias) for alias in aliases]
            if any(((alias.isascii() and len(alias) <= 3) or (not alias.isascii() and len(alias) <= 2)) and alias in compact_evidence for alias in compact_aliases):
                return EntityMatchResult(None, 0.0, supporting_signals=("short_name_needs_context",), official_domain_match=official_domain_match)
            return EntityMatchResult(True, 0.68, supporting_signals=("official_source_without_name",), official_domain_match=False)
        # VNDB screenshots are retained as a trusted-database fallback for
        # legacy issues; they remain lower-trust and are still subject to the
        # image-type gate and review metadata.
        if candidate.source_type is SourceType.UNVERIFIED and candidate_domain.endswith(("vndb.org", "vndb.net")):
            return EntityMatchResult(True, 0.82, supporting_signals=("trusted_database_context",), official_domain_match=False)
        if supporting:
            # Numeric parser evidence is useful, but without a named entity it
            # is not enough to call a third-party image a safe match.
            return EntityMatchResult(None, 0.0, supporting_signals=tuple(dict.fromkeys([*supporting, "no_entity_name"])), official_domain_match=official_domain_match)
        return EntityMatchResult(None, 0.0, supporting_signals=("no_entity_context",), official_domain_match=official_domain_match)


__all__ = ["EntityMatchResult", "EntityMatcher"]
