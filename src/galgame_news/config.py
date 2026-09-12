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
    duplicate_penalty: float = Field(ge=0.0, le=1.0)
    risk_penalty: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> "ScoringConfig":
        if abs((self.relevance + self.freshness + self.source_trust + self.quality) - 1.0) > 1e-6:
            raise ValueError("scoring weights must sum to 1")
        return self


class FilterConfig(_ConfigModel):
    min_width: int = Field(ge=1)
    min_height: int = Field(ge=1)
    min_pixels: int = Field(ge=1)
    max_response_bytes: int = Field(ge=1)
    max_image_bytes: int = Field(ge=1)


class NetworkConfig(_ConfigModel):
    timeout_seconds: float = Field(gt=0)
    max_retries: int = Field(ge=0, le=10)
    user_agent: str = Field(min_length=1)


class SelectionConfig(_ConfigModel):
    min_images: int = Field(ge=0)
    max_images: int = Field(ge=1)
    per_news_max: int = Field(ge=1)
    minimum_score: float = Field(ge=0.0, le=100.0)

    @model_validator(mode="after")
    def limits_are_ordered(self) -> "SelectionConfig":
        if self.min_images > self.max_images:
            raise ValueError("min_images cannot exceed max_images")
        return self


class SearchConfig(_ConfigModel):
    max_results: int = Field(ge=1)
    same_domain_depth: int = Field(ge=0, le=3)


class PrescanConfig(_ConfigModel):
    scoring: ScoringConfig
    filters: FilterConfig
    network: NetworkConfig
    selection: SelectionConfig
    search: SearchConfig


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
    default_path = _default_path()
    if not default_path.is_file():
        raise FileNotFoundError(f"default configuration is missing: {default_path}")
    try:
        with default_path.open("rb") as handle:
            payload = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid default configuration: {exc}") from exc
    if path is not None:
        selected = Path(path)
        if not selected.is_file():
            raise FileNotFoundError(f"configuration is missing: {selected}")
        try:
            with selected.open("rb") as handle:
                payload = _merge(payload, tomllib.load(handle))
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"invalid configuration: {exc}") from exc
    try:
        return PrescanConfig.model_validate(payload)
    except Exception as exc:
        raise ValueError(f"invalid prescan configuration: {exc}") from exc
