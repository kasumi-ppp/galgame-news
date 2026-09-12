"""Backward-compatible entry point for the legacy image prescan script.

The maintained implementation lives in ``src/galgame_news``.  This module
keeps the historical imports and command invocation working while the old
single-file implementation is archived under ``legacy/``.
"""

from legacy import image_prescan_legacy as _legacy

# Preserve the historical module surface, including private helpers that
# existing tests and integrations may patch.
globals().update(
    {
        name: value
        for name, value in vars(_legacy).items()
        if name not in {"__name__", "__package__", "__loader__", "__spec__"}
    }
)


if __name__ == "__main__":
    from legacy.image_prescan_legacy import main

    main()
