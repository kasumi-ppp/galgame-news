"""Lazy, bounded thumbnail cache used by the review page."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from typing import Generic, TypeVar


T = TypeVar("T")


class ThumbnailCache(Generic[T]):
    """A tiny LRU cache.

    Values are created by the caller supplied factory.  This keeps image
    decoding out of the list population path and makes the cache useful for
    both ``QPixmap`` values and light-weight test doubles.
    """

    def __init__(self, max_items: int = 128):
        if int(max_items) < 1:
            raise ValueError("max_items must be positive")
        self.max_items = int(max_items)
        self._values: OrderedDict[str, T] = OrderedDict()

    def get(self, key: str, factory: Callable[[], T] | None = None) -> T | None:
        key = str(key)
        if key in self._values:
            value = self._values.pop(key)
            self._values[key] = value
            return value
        if factory is None:
            return None
        value = factory()
        self.put(key, value)
        return value

    def put(self, key: str, value: T) -> T:
        key = str(key)
        self._values.pop(key, None)
        self._values[key] = value
        while len(self._values) > self.max_items:
            self._values.popitem(last=False)
        return value

    def contains(self, key: str) -> bool:
        return str(key) in self._values

    def clear(self) -> None:
        self._values.clear()

    def __contains__(self, key: object) -> bool:
        return str(key) in self._values

    def __len__(self) -> int:
        return len(self._values)

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(self._values)


__all__ = ["ThumbnailCache"]
