"""Conservative image selection policy for localization news."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..delivery.helpers import section_prefix
from ..domain import ImageType


@dataclass(frozen=True)
class LocalizationDecision:
    image_type: ImageType
    confidence: float
    auto_select: bool
    reasons: tuple[str, ...]
    rank_priority: int
    entity_confirmed: bool


class LocalizationImagePolicy:
    """Evaluate downloaded, work-bound images for section-H news."""

    def evaluate(self, news, candidate, source_linked: bool) -> LocalizationDecision:
        reasons: list[str] = []
        provenance = getattr(candidate, "localization_provenance", None)
        signals = getattr(candidate, "signals", {}) or {}

        def value(key: str, default=None):
            from_provenance = getattr(provenance, key, None) if provenance is not None else None
            return from_provenance if from_provenance is not None else signals.get(f"localization_{key}", default)

        if section_prefix(getattr(news, "section", "")) != "h":
            return self._review("not_localization_section")

        source = str(value("source", "")).casefold()
        work_id = str(value("work_id", "")).strip()
        binding = str(value("binding", "uncertain")).casefold()
        resolution = str(value("resolution", "unknown")).casefold()
        screenshot = value("screenshot", False) is True
        native = value("native", False) is True

        roles = {str(getattr(ev, "role", "")).casefold() for ev in getattr(candidate, "evidence", ())}
        if roles.intersection({"logo", "decorative"}):
            return self._review("localization_non_game_image_role")
        if binding in {"conflict", "mismatch", "entity_conflict"} or signals.get("localization_binding_conflict") is True:
            return self._review("localization_binding_conflict")

        expected_ids = set()
        context = getattr(news, "localization_context", None)
        if context is not None:
            expected_ids = set(context.vndb_ids if source == "vndb" else context.steam_app_ids if source == "steam" else ())
        entity_matched = bool(source in {"vndb", "steam"} and work_id and work_id in expected_ids)
        entity_confirmed = entity_matched and binding == "confirmed"
        if not entity_confirmed:
            reasons.append("localization_work_not_confirmed")
        if binding == "uncertain":
            reasons.append("localization_binding_uncertain")
        elif binding == "reference":
            reasons.append("localization_reference_only")

        # Only image-local role evidence can promote an image to CG.
        event_cg = "event_cg" in roles

        dims_ok, dims_reason = self._dimensions(candidate)
        valid = self._validated(candidate) and dims_ok
        if not self._validated(candidate):
            reasons.append("localization_image_not_downloaded_or_decoded")
        if dims_reason:
            reasons.append(dims_reason)
        if not source_linked:
            reasons.append("localization_source_unlinked")
        if not screenshot:
            reasons.append("localization_image_not_screenshot")
        if not native:
            reasons.append("localization_image_not_native")
        if resolution != "full":
            reasons.append("localization_not_full_resolution")

        qualified = entity_confirmed and source_linked and screenshot and native and resolution == "full" and valid
        if candidate.animated_source or signals.get("animated_source") is True:
            qualified = False
            reasons.append("animated_source_requires_review")
        if qualified:
            if event_cg:
                return LocalizationDecision(ImageType.GAME_CG, 0.92, True, ("image_local_event_cg", "localization_work_confirmed", "source_linked", "decoded_image_valid"), 100, True)
            return LocalizationDecision(ImageType.GAMEPLAY_SCREENSHOT, 0.90, True, ("localization_full_native_screenshot", "localization_work_confirmed", "source_linked", "decoded_image_valid"), 90, True)
        if entity_matched and source_linked and screenshot and self._validated(candidate):
            # Keep attributable but non-qualifying screenshots visible for review.
            reasons.append("localization_manual_review_required")
            if event_cg:
                return LocalizationDecision(ImageType.GAME_CG, 0.85, False, tuple(dict.fromkeys(["image_local_event_cg", *reasons])), 100, entity_confirmed)
            return LocalizationDecision(ImageType.GAMEPLAY_SCREENSHOT, 0.85, False, tuple(dict.fromkeys(reasons)), 90, entity_confirmed)
        return LocalizationDecision(ImageType.UNKNOWN, 0.0, False, tuple(reasons or ["localization_review_required"]), 0, entity_confirmed)

    @staticmethod
    def _validated(candidate) -> bool:
        signals = getattr(candidate, "signals", {}) or {}
        status = getattr(candidate, "download_status", "")
        downloaded = status == "downloaded" or signals.get("download_success") is True
        decoded = bool(getattr(candidate, "width", None) and getattr(candidate, "height", None))
        converted = not bool(signals.get("conversion_error"))
        path = getattr(candidate, "local_path", None)
        reviewable = bool(path and Path(path).is_file())
        return downloaded and decoded and converted and reviewable and not signals.get("invalid_reason")

    @staticmethod
    def _dimensions(candidate) -> tuple[bool, str | None]:
        width = getattr(candidate, "width", None)
        height = getattr(candidate, "height", None)
        if not width or not height:
            return False, "localization_dimensions_missing"
        expected_w = getattr(candidate, "expected_width", None)
        expected_h = getattr(candidate, "expected_height", None)
        if expected_w and expected_h and (width, height) != (expected_w, expected_h):
            return False, "localization_expected_dimensions_mismatch"
        if max(width, height) < 800 or min(width, height) < 450 or width * height < 360_000:
            return False, "localization_below_native_resolution_floor"
        return True, None

    @staticmethod
    def _review(reason: str) -> LocalizationDecision:
        return LocalizationDecision(ImageType.UNKNOWN, 0.0, False, (reason,), 0, False)


# Concise alias for callers that name the policy by its domain.
LocalizationPolicy = LocalizationImagePolicy
