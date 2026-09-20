from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_pyinstaller_spec_is_syntax_valid_and_targets_portable_executable():
    spec = ROOT / "packaging" / "GalgameNewsToolbox.spec"
    tree = ast.parse(spec.read_text(encoding="utf-8"), filename=str(spec))
    assert tree
    source = spec.read_text(encoding="utf-8")
    assert "GalgameNewsToolbox" in source
    assert "GalgameNewsToolbox.exe" in source
    assert "bin" in source
    assert "config" in source
    assert '"desktop_entry.py"' in source
    assert "console=False" in source
    assert 'contents_directory="."' in source


def test_desktop_build_entry_uses_absolute_import():
    source = (ROOT / "packaging" / "desktop_entry.py").read_text(encoding="utf-8")
    assert "from galgame_news.desktop.app import main" in source


def test_desktop_entry_has_writable_app_data_fallback():
    source = (ROOT / "packaging" / "desktop_entry.py").read_text(encoding="utf-8")
    assert "_prepare_writable_app_data" in source
    assert 'os.environ["LOCALAPPDATA"]' in source
    assert "tempfile.gettempdir" in source


def test_build_entry_is_importable_without_pyinstaller():
    source = (ROOT / "packaging" / "build.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="build.py")
    assert tree
    assert "PyInstaller" in source
    assert "download" not in source.casefold()
