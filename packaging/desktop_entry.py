"""PyInstaller entry point for the Windows desktop application."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import sys


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


def _configure_portable_runtime() -> None:
    if getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent
        os.chdir(root)
        if (root / "browser").is_dir():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(root / "browser")


def _import_private_test_key() -> None:
    """The optional private-release seed is never part of the source build."""
    if not getattr(sys, "frozen", False):
        return
    seed = Path(sys.executable).resolve().parent / ".private-socialdata-key"
    if not seed.is_file():
        return
    from galgame_news.settings import CredentialStore

    store = CredentialStore()
    if not store.has("socialdata_api_key"):
        value = seed.read_text(encoding="utf-8").strip()
        if not value or "\n" in value or "\r" in value:
            raise RuntimeError("私测授权文件无效，请联系测试包提供者。")
        store.set("socialdata_api_key", value)
    # The original ZIP still contains the authorized seed for other testers.
    seed.unlink()


_configure_portable_runtime()

from galgame_news.desktop.app import main


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        import json
        from galgame_news.desktop.app import create_application
        from galgame_news.settings.ffmpeg import discover_ffmpeg
        from galgame_news.settings.browser import check_browser_runtime
        from galgame_news.config import load_config
        from playwright.sync_api import sync_playwright

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app, window = create_application([])
        browser = check_browser_runtime()
        launched = False
        with sync_playwright() as playwright:
            chromium = playwright.chromium.launch(headless=True)
            page = chromium.new_page()
            page.set_content("<html><body>portable offline check</body></html>")
            launched = "portable offline check" in page.content()
            chromium.close()
        ffmpeg = discover_ffmpeg()
        root = Path(sys.executable).resolve().parent
        report = {"desktop": True, "browser_available": browser.available,
                  "browser_launch": launched, "ffmpeg": ffmpeg.available,
                  "ffprobe": bool(ffmpeg.ffprobe_path), "config": bool(load_config()),
                  "private_key_seed_present": (root / ".private-socialdata-key").is_file()}
        (root / "package_self_check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        window.close()
        raise SystemExit(0 if all(report.values()) else 1)
    _import_private_test_key()
    raise SystemExit(main())
