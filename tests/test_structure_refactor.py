from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pipeline_structure_helpers_are_available_without_runner_import_cycles():
    from galgame_news.pipeline.checkpoint import sha256_path
    from galgame_news.pipeline.workspace import safe_task_label

    assert safe_task_label("第259期") == "第259期"
    assert sha256_path(ROOT / "pyproject.toml")


def test_adapters_package_preserves_existing_public_import_surface():
    from galgame_news.discovery.adapters import (
        DirectImageAdapter,
        DynamicPageAdapter,
        OfficialHtmlAdapter,
        SteamAdapter,
        VideoAdapter,
        XAdapter,
    )

    assert all(
        adapter.__module__.startswith("galgame_news.discovery.adapters")
        for adapter in (
            DirectImageAdapter,
            DynamicPageAdapter,
            OfficialHtmlAdapter,
            SteamAdapter,
            VideoAdapter,
            XAdapter,
        )
    )


def test_retired_image_prescan_entrypoint_does_not_import_legacy_engine():
    source = (ROOT / "image_prescan.py").read_text(encoding="utf-8")
    assert "image_prescan_legacy" not in source
    assert "python -m galgame_news run" in source
