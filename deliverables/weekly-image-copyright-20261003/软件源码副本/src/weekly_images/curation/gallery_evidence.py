"""News-local, pixel-confirmed recovery of gallery evidence in old indexes."""

from collections import defaultdict
import json
from urllib.parse import urlsplit, urlunsplit

from ..domain import ImageCandidate, ImageType, SourceType
from .dedup import _confirmed_visual_duplicate


DERIVED_SIGNALS = frozenset({
    "image_type_supporting_signals", "type_match", "auto_select", "fallback_only",
    "entity_match", "entity_match_confidence", "entity_match_evidence", "entity_conflict",
    "official_domain_match", "source_tier", "source_is_official", "source_linked",
    "candidate_status", "quality_eligible", "duplicate_of", "duplicate_kind",
    "duplicate_reason", "possible_duplicate_of",
})


def reset_selection(candidate: ImageCandidate) -> None:
    """Keep downloaded assets and raw evidence, discard prior policy results."""
    recovered = candidate.signals.pop("gallery_recovery_original", None)
    if isinstance(recovered, str):
        try:
            snapshot = json.loads(recovered)
            if not isinstance(snapshot.get("signals"), dict) or not isinstance(snapshot.get("fields"), dict):
                raise ValueError("malformed recovery snapshot")
        except (ValueError, TypeError, AttributeError):
            snapshot = {"signals": {}, "fields": {}}
        for key in ("gallery_path", "gallery_evidence_source", "gallery_evidence_from", "media_variant_of", "root_source_url", "parent_source_url"):
            if snapshot["signals"].get(key) is None:
                candidate.signals.pop(key, None)
            else:
                candidate.signals[key] = snapshot["signals"][key]
        for key in ("news_source_url", "parent_source_url", "image_alt", "nearby_text"):
            if key in snapshot["fields"] and (snapshot["fields"][key] is None or isinstance(snapshot["fields"][key], str)):
                setattr(candidate, key, snapshot["fields"][key])
    candidate.selected = False
    candidate.score = None
    candidate.image_type = ImageType.UNKNOWN
    candidate.image_type_confidence = 0.0
    candidate.selection_reasons.clear()
    candidate.review_reasons = [r for r in candidate.review_reasons if r.value not in {
        "uncertain_match", "image_type_review", "image_quality_review", "unknown_publish_time",
    }]
    for key in DERIVED_SIGNALS:
        candidate.signals.pop(key, None)


def _page_key(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme.casefold(), p.netloc.casefold(), p.path.rstrip("/"), p.query, ""))


def inherit_gallery_evidence(news_by_id, candidates: list[ImageCandidate], linked_gallery, classifier, entity_matches) -> None:
    """Recover only from a verified CG sibling on the exact same news/page.

    A filename, pHash, or DOM relation alone never proves identity here. The
    downloaded pixels must confirm the same scene. Only original seeds donate
    evidence, preventing transitive propagation through similar scenes.
    """
    groups = defaultdict(list)
    for candidate in candidates:
        if candidate.source_type is SourceType.OFFICIAL_SITE and not candidate.signals.get("invalid_reason"):
            groups[candidate.news_id, _page_key(candidate.source_url)].append(candidate)
    for (news_id, _), group in groups.items():
        news = news_by_id.get(news_id)
        if news is None:
            continue
        seeds = [c for c in group if linked_gallery(news, c)
                 and not c.signals.get("gallery_excluded_reason")
                 and classifier.classify(news, c).image_type is ImageType.GAME_CG
                 and entity_matches(news, c)]
        for candidate in group:
            if candidate.signals.get("gallery_path") is True or candidate.signals.get("gallery_excluded_reason"):
                continue
            donor = next((seed for seed in seeds if _confirmed_visual_duplicate(candidate, seed)), None)
            if donor is None:
                continue
            candidate.signals["gallery_recovery_original"] = json.dumps({
                "signals": {key: candidate.signals.get(key) for key in (
                    "gallery_path", "gallery_evidence_source", "gallery_evidence_from", "media_variant_of", "root_source_url", "parent_source_url")},
                "fields": {key: getattr(candidate, key) for key in (
                    "news_source_url", "parent_source_url", "image_alt", "nearby_text")},
            }, ensure_ascii=False)
            # Keep any explicit entity contradiction; the matcher evaluates it
            # even when same-scene evidence has been recovered.
            candidate.signals["gallery_path"] = True
            candidate.signals["gallery_evidence_source"] = "same_news_source_decoded_pixels"
            candidate.signals["gallery_evidence_from"] = str(donor.id)
            candidate.signals["media_variant_of"] = donor.image_url
            for key in ("root_source_url", "parent_source_url"):
                if not candidate.signals.get(key) and donor.signals.get(key):
                    candidate.signals[key] = donor.signals[key]
            candidate.news_source_url = candidate.news_source_url or donor.news_source_url
            candidate.parent_source_url = candidate.parent_source_url or donor.parent_source_url
            candidate.image_alt = candidate.image_alt or donor.image_alt
            candidate.nearby_text = candidate.nearby_text or donor.nearby_text
