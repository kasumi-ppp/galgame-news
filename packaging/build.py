"""Build the Windows portable onedir artifact with an installed PyInstaller."""

from __future__ import annotations

from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = PROJECT_ROOT / "packaging" / "GalgameNewsToolbox.spec"


def build(*, clean: bool = True, pyinstaller_main: Any | None = None) -> int:
    """Invoke PyInstaller without installing or fetching any dependency."""

    if pyinstaller_main is None:
        try:
            from PyInstaller import __main__ as pyinstaller_main
        except ImportError as exc:
            raise RuntimeError(
                "PyInstaller is required; install the optional build extra"
            ) from exc
    arguments = ["--noconfirm"]
    if clean:
        arguments.append("--clean")
    arguments.append(str(SPEC_PATH))
    pyinstaller_main.run(arguments)
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
