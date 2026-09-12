"""TOML-backed validation for curation and networking policy."""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScoringConfig(_ConfigModel):
    relevance: float = Field(ge=0.0, le=1.0)
    freshness: float = Field(ge=0.0, le=1.0)
    source_trust: float = Field(ge=0.0, le=1.0)
    quality: float = Field(ge=0.0, le=1.0)
    duplicate_penalty: float = Field(default=0.0, ge=0.0, le=1.0)
    risk_penalty: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> "ScoringConfig":
        if abs((self.relevance + self.freshness + self.source_trust + self.quality) - 1.0) > 1e-6:
            raise ValueError("scoring weights must sum to 1")
        return self


class FilterConfig(_ConfigModel):
    min_width: int = Field(ge=1)
    min_height: int = Field(ge=1)
    min_pixels: int = Field(ge=1)
    max_response_bytes: int = Field(default=5 * 1024 * 1024, ge=1)
    max_image_bytes: int = Field(default=12 * 1024 * 1024, ge=1)


class NetworkConfig(_ConfigModel):
    timeout_seconds: float = Field(default=12.0, gt=0)
    max_retries: int = Field(default=3, ge=0, le=10)
    user_agent: str = Field(default="WeeklyGalgameImagePrescan/2.0", min_length=1)


class SelectionConfig(_ConfigModel):
    min_images: int = Field(default=5, ge=0)
    max_images: int = Field(default=20, ge=1)
    per_news_max: int = Field(default=3, ge=1)
    minimum_score: float = Field(default=0.0, ge=0.0, le=100.0)

    @model_validator(mode="after")
    def limits_are_ordered(self) -> "SelectionConfig":
        if self.min_images > self.max_images:
            raise ValueError("min_images cannot exceed max_images")
        return self


class SearchConfig(_ConfigModel):
    max_results: int = Field(default=10, ge=1)
    same_domain_depth: int = Field(default=1, ge=0, le=3)


class PrescanConfig(_ConfigModel):
    scoring: ScoringConfig
    filters: FilterConfig
    network: NetworkConfig = NetworkConfig()
    selection: SelectionConfig = SelectionConfig()
    search: SearchConfig = SearchConfig()


_FALLBACK_DEFAULTS = {
    "scoring": {
        "relevance": 0.50,
        "freshness": 0.20,
        "source_trust": 0.20,
        "quality": 0.10,
    },
    "filters": {"min_width": 300, "min_height": 300, "min_pixels": 120000},
    "network": {"timeout_seconds": 12.0, "max_retries": 3, "user_agent": "WeeklyGalgameImagePrescan/2.0"},
    "selection": {"min_images": 5, "max_images": 20, "per_news_max": 3, "minimum_score": 0.0},
    "search": {"max_results": 10, "same_domain_depth": 1},
}


def _merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _default_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "default.toml"


def load_config(path: Path | str | None = None) -> PrescanConfig:
    """Load repository defaults and recursively overlay an optional TOML file."""
    payload = copy.deepcopy(_FALLBACK_DEFAULTS)
    default_path = _default_path()
    if default_path.is_file():
        with default_path.open("rb") as handle:
            payload = _merge(payload, tomllib.load(handle))
    if path is not None:
        selected = Path(path)
        with selected.open("rb") as handle:
            payload = _merge(payload, tomllib.load(handle))
    try:
        return PrescanConfig.model_validate(payload)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"invalid prescan configuration: {exc}") from exc
