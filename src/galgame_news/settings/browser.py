"""Optional browser runtime diagnostics and explicit installer commands."""

from __future__ import annotations

import sys
from pathlib import Path

from ..discovery.browser import BrowserRuntimeStatus, check_browser_runtime


PLAYWRIGHT_VERSION = "1.63.0"


def browser_install_commands(python_executable: str | None = None) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return shell-free commands for an explicit, pinned browser install."""

    python = python_executable or sys.executable
    if python_executable is None and getattr(sys, "frozen", False):
        raise RuntimeError("打包版请使用包含浏览器组件的安装包；不能把应用程序当成 Python 安装器。")
    if Path(python).name.casefold() == "pythonw.exe":
        console_python = Path(python).with_name("python.exe")
        if console_python.is_file():
            python = str(console_python)
    return (
        (python, ("-m", "pip", "install", f"playwright=={PLAYWRIGHT_VERSION}")),
        (python, ("-m", "playwright", "install", "chromium")),
    )


__all__ = ["BrowserRuntimeStatus", "PLAYWRIGHT_VERSION", "browser_install_commands", "check_browser_runtime"]
