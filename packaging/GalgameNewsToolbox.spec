"""PyInstaller onedir specification for the portable toolbox.

The optional ``bin`` directory is copied only when a maintainer has supplied
local binaries.  This file never downloads or vendors FFmpeg.
"""

from pathlib import Path


SPEC_ROOT = Path(SPECPATH).resolve()
PROJECT_ROOT = SPEC_ROOT.parent
SRC_ROOT = PROJECT_ROOT / "src"
# PyInstaller appends .exe to this name on Windows.
TARGET_EXECUTABLE = "GalgameNewsToolbox.exe"


def _data_files() -> list[tuple[str, str]]:
    data: list[tuple[str, str]] = []
    for directory_name in ("config", "assets"):
        directory = PROJECT_ROOT / directory_name
        if directory.is_dir():
            data.append((str(directory), directory_name))

    # A release may provide a local bin/ directory, but no binary is fetched
    # by this spec and the repository intentionally does not contain FFmpeg.
    bin_root = PROJECT_ROOT / "bin"
    if bin_root.is_dir():
        for file_path in sorted(path for path in bin_root.rglob("*") if path.is_file()):
            relative_parent = file_path.relative_to(bin_root).parent
            data.append((str(file_path), str(Path("bin") / relative_parent)))
    return data


a = Analysis(
    [str(SPEC_ROOT / "desktop_entry.py")],
    pathex=[str(SRC_ROOT)],
    binaries=[],
    datas=_data_files(),
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GalgameNewsToolbox",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    # Keep data files beside the executable.  ``config._default_path`` uses
    # the frozen package location's bundle root, while PyInstaller 6 defaults
    # supporting files to ``_internal``.
    contents_directory=".",
    name="GalgameNewsToolbox",
)
