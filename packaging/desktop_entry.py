"""PyInstaller entry point for the Windows desktop application."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile


def _prepare_writable_app_data() -> None:
    """Keep the packaged launcher usable when LOCALAPPDATA is read-only."""

    if os.name != "nt":
        return

    configured_base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if configured_base:
        configured_root = Path(configured_base) / "GalgameNewsToolbox"
        probe = configured_root / ".write-probe"
        try:
            configured_root.mkdir(parents=True, exist_ok=True)
            probe.touch()
            probe.unlink()
            return
        except OSError:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass

    fallback_base = Path(tempfile.gettempdir())
    fallback_root = fallback_base / "GalgameNewsToolbox"
    probe = fallback_root / ".write-probe"
    try:
        fallback_root.mkdir(parents=True, exist_ok=True)
        probe.touch()
        probe.unlink()
    except OSError as exc:
        raise RuntimeError("no writable application-data directory is available") from exc
    os.environ["LOCALAPPDATA"] = str(fallback_base)


_prepare_writable_app_data()

from galgame_news.desktop.app import main


if __name__ == "__main__":
    raise SystemExit(main())
