from pathlib import Path

import pytest

from galgame_news.config import PrescanConfig, load_config


def test_load_config_reads_repository_defaults():
    config = load_config()
    assert config.scoring.relevance == pytest.approx(0.50)
    assert config.scoring.freshness == pytest.approx(0.20)
    assert config.filters.min_width == 300
    assert config.selection.max_images == 20
    assert config.search.max_candidates_per_source >= 100
    assert config.image_types.scene_aspect_ratio > 1.0
    assert config.image_types.minimum_gallery_group_size >= 2


def test_config_rejects_weights_that_do_not_sum_to_one(tmp_path: Path):
    path = tmp_path / "invalid.toml"
    path.write_text(
        "[scoring]\nrelevance=0.7\nfreshness=0.2\nsource_trust=0.2\nquality=0.1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="sum"):
        load_config(path)


def test_config_rejects_non_positive_image_limits(tmp_path: Path):
    path = tmp_path / "invalid.toml"
    path.write_text(
        """[scoring]
relevance=0.5
freshness=0.2
source_trust=0.2
quality=0.1
[filters]
min_width=0
min_height=300
min_pixels=120000
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="min_width"):
        load_config(path)


def test_config_defaults_are_loaded_from_an_explicit_file(tmp_path: Path):
    path = tmp_path / "custom.toml"
    path.write_text(
        """[scoring]
relevance=0.6
freshness=0.1
source_trust=0.2
quality=0.1
[filters]
min_width=640
min_height=360
min_pixels=230400
[selection]
min_images=3
max_images=12
per_news_max=2
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.filters.min_width == 640
    assert config.selection.max_images == 12
    assert config.selection.per_news_max == 2


def test_config_reports_missing_default_file_explicitly(monkeypatch, tmp_path: Path):
    import galgame_news.config as config_module

    missing = tmp_path / "missing-default.toml"
    monkeypatch.setattr(config_module, "_default_path", lambda: missing)

    with pytest.raises(FileNotFoundError, match="default configuration"):
        load_config()
