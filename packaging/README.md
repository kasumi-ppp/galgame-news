# Windows portable build

The release artifact is a PyInstaller onedir bundle named
`GalgameNewsToolbox`. Its executable is `GalgameNewsToolbox.exe` on Windows.

Install the pinned build tooling in a clean virtual environment, then run:

```powershell
python -m pip install -e ".[desktop,dev,build]"
python packaging/build.py
```

The generated bundle is staged outside the repository and then overlaid below
`dist/GalgameNewsToolbox/`. The spec includes only `config/default.toml`, plus
an optional repository `assets/` directory. A release maintainer may place
already-acquired `ffmpeg.exe` and `ffprobe.exe` below `bin/`; the spec copies
those files when present. The build never downloads FFmpeg and no FFmpeg
binaries are committed here.

The publisher preserves unknown top-level bundle files and all
`dist/.../output` user data. The managed `_internal` runtime tree is replaced
as a whole on each successful publish, so stale Qt/PySide6 DLLs cannot remain
from an older build. If copying fails, the previous runtime tree is restored.
If the target executable is in use, publication fails before copying files.
After a successful Windows build, `00_启动工具箱.lnk` points to the bundle
executable with the repository root as its working directory.

Completed toolbox tasks retain their full task directory, including `raw/`,
indexes, checkpoints, caches, review state, logs, and `final/` image delivery.
The history page can therefore reopen review state or resume recoverable work.
Older tasks that were already compacted remain image-only: missing internal
data is not reconstructed from `final/images`.

The spec keeps PySide6 as the only Qt binding and explicitly excludes PyQt5,
PyQt6, PySide2, tkinter, test/notebook/documentation modules, and unused
scientific stacks such as NumPy and Matplotlib. These packages are not imported
by the production entry point; project runtime dependencies remain included.

Desktop UI and keyring support are optional extras. The headless CLI must be
usable from the base installation, so do not import PySide6 or keyring from
core modules at startup.

Release policy: publish a reproducible bundle from a clean checkout, keep the
versions in `pyproject.toml` and `requirements-build.txt` fixed for each
release, and update them deliberately with a release note. There is no
auto-updater; distribute and replace bundles through the normal release
process.

## Windows smoke check

After building, run the executable from PowerShell:

```powershell
& .\dist\GalgameNewsToolbox\GalgameNewsToolbox.exe
```

Confirm the five pages open, create a three-news offline fixture task, safely
stop and resume it, open the review page, and export reviewed media. Confirm
the completed task retains its `raw/`, checkpoint, cache, review-state, and
`final/` directories. Also check
that Settings reports the bundled FFmpeg/ffprobe paths when `bin/ffmpeg.exe`
and `bin/ffprobe.exe` were included.

Before publishing the portable ZIP, manually verify on a clean Windows
machine with no Python installation:

- the application starts from the extracted ZIP;
- image and video collection complete with the bundled tools;
- safe stop, restart, history, and review decisions survive an app restart;
- a single-news retry does not overwrite `raw/`;
- exported files retain the `x1.01.jpg`, `h1.01.jpg`, and `z1.01.jpg` naming;
- API credentials do not appear in JSON, logs, output, or crash reports;
- third-party notices and the pinned component versions ship with the bundle.

This repository checklist is documented but has not yet been executed on a
separate clean Windows machine.
