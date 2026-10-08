from datetime import datetime, timezone
import pytest

from galgame_news.domain import (
    ImageCandidate,
    ImageType,
    LocalizationContext,
    LocalizationProvenance,
    NewsItem,
)
from galgame_news.localization.policy import LocalizationImagePolicy


def make_news(section="汉化"):
    return NewsItem(
        issue_id="issue",
        sequence=1,
        section=section,
        title="Localization update",
        body="",
        localization_context=LocalizationContext(vndb_ids=["v123"], steam_app_ids=["456"], source_urls=["https://example.test"]),
    )


def make_candidate(tmp_path, **overrides):
    data = dict(
        news_id="news",
        image_url="https://cdn.example.test/image.jpg",
        source_url="https://store.steampowered.com/app/456/",
        source_type="steam",
        fetched_at=datetime.now(timezone.utc),
        download_status="downloaded",
        width=1280,
        height=720,
        local_path=str(tmp_path / "candidate.jpg"),
        localization_provenance=LocalizationProvenance(
            source="steam", work_id="456", binding="confirmed", resolution="full", screenshot=True, native=True
        ),
    )
    data.update(overrides)
    if data.get("local_path"):
        path = tmp_path / "candidate.jpg"
        path.write_bytes(b"reviewable test image placeholder")
        data["local_path"] = str(path)
    return ImageCandidate(**data)


def test_full_native_work_bound_screenshot_auto_selects(tmp_path):
    decision = LocalizationImagePolicy().evaluate(make_news(), make_candidate(tmp_path), source_linked=True)
    assert decision.image_type is ImageType.GAMEPLAY_SCREENSHOT
    assert decision.confidence == 0.90
    assert decision.auto_select is True
    assert decision.entity_confirmed is True


@pytest.mark.parametrize("binding", ["reference", "uncertain"])
def test_non_confirmed_binding_never_auto_selects(binding, tmp_path):
    candidate = make_candidate(tmp_path, localization_provenance=LocalizationProvenance(
        source="steam", work_id="456", binding=binding, resolution="full", screenshot=True, native=True
    ))
    decision = LocalizationImagePolicy().evaluate(make_news(), candidate, source_linked=True)
    assert decision.auto_select is False
    assert decision.image_type is ImageType.GAMEPLAY_SCREENSHOT
    assert decision.confidence == 0.85


def test_screenshot_requires_source_link_and_image_level_screenshot_evidence(tmp_path):
    candidate = make_candidate(tmp_path, localization_provenance=LocalizationProvenance(
        source="steam", work_id="456", binding="confirmed", resolution="full", screenshot=False, native=True
    ))
    decision = LocalizationImagePolicy().evaluate(make_news(), candidate, source_linked=False)
    assert decision.auto_select is False
    assert "localization_source_unlinked" in decision.reasons
    assert "localization_image_not_screenshot" in decision.reasons


@pytest.mark.parametrize("width,height", [(799, 720), (1280, 449), (600, 600)])
def test_native_resolution_floor_is_required(width, height, tmp_path):
    candidate = make_candidate(tmp_path, width=width, height=height)
    decision = LocalizationImagePolicy().evaluate(make_news(), candidate, source_linked=True)
    assert not decision.auto_select
    assert "localization_below_native_resolution_floor" in decision.reasons


def test_expected_dimension_mismatch_and_conversion_error_block_selection(tmp_path):
    candidate = make_candidate(tmp_path, expected_width=1920, expected_height=1080, signals={"conversion_error": "decode failed"})
    decision = LocalizationImagePolicy().evaluate(make_news(), candidate, source_linked=True)
    assert not decision.auto_select
    assert "localization_expected_dimensions_mismatch" in decision.reasons
    assert "localization_image_not_downloaded_or_decoded" in decision.reasons


def test_only_image_level_event_cg_is_classified_as_cg_and_ranked_first(tmp_path):
    candidate = make_candidate(tmp_path, evidence=[{"page_url": "https://example.test", "role": "event_cg"}])
    decision = LocalizationImagePolicy().evaluate(make_news(), candidate, source_linked=True)
    assert decision.image_type is ImageType.GAME_CG
    assert decision.confidence == 0.92
    assert decision.rank_priority > 90


def test_event_cg_thumbnail_is_review_only_and_requires_all_screenshot_gates(tmp_path):
    candidate = make_candidate(tmp_path, localization_provenance=LocalizationProvenance(
        source="steam", work_id="456", binding="confirmed", resolution="thumbnail", screenshot=True, native=True
    ), evidence=[{"page_url": "https://example.test", "role": "event_cg"}])
    decision = LocalizationImagePolicy().evaluate(make_news(), candidate, source_linked=True)
    assert decision.image_type is ImageType.GAME_CG
    assert not decision.auto_select


@pytest.mark.parametrize("role", ["logo", "decorative"])
def test_logo_and_decorative_image_roles_are_rejected(role, tmp_path):
    candidate = make_candidate(tmp_path, evidence=[{"page_url": "https://example.test", "role": role}])
    decision = LocalizationImagePolicy().evaluate(make_news(), candidate, source_linked=True)
    assert decision.image_type is ImageType.UNKNOWN
    assert not decision.auto_select


def test_explicit_binding_conflict_is_rejected(tmp_path):
    candidate = make_candidate(tmp_path, localization_provenance=LocalizationProvenance(
        source="steam", work_id="456", binding="conflict", resolution="full", screenshot=True, native=True
    ))
    decision = LocalizationImagePolicy().evaluate(make_news(), candidate, source_linked=True)
    assert decision.image_type is ImageType.UNKNOWN
    assert "localization_binding_conflict" in decision.reasons


def test_common_gallery_roles_and_non_h_sections_do_not_promote(tmp_path):
    candidate = make_candidate(tmp_path, evidence=[{"page_url": "https://example.test", "role": "character_art"}])
    decision = LocalizationImagePolicy().evaluate(make_news("新作"), candidate, source_linked=True)
    assert decision.image_type is ImageType.UNKNOWN
    assert not decision.auto_select


def test_local_path_is_required_for_reviewable_auto_selection(tmp_path):
    candidate = make_candidate(tmp_path)
    decision = LocalizationImagePolicy().evaluate(make_news(), candidate, source_linked=True)
    assert decision.auto_select


def test_missing_local_path_never_auto_selects(tmp_path):
    candidate = make_candidate(tmp_path, local_path=None)
    decision = LocalizationImagePolicy().evaluate(make_news(), candidate, source_linked=True)
    assert not decision.auto_select
