"""Small internal models for localization source lookup."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VNDBWork:
    id: str
    title: str = ""
    screenshots: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class VNDBRelease:
    id: str
    vn: dict[str, Any] | None = None
