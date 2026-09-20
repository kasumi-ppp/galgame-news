# Windows portable build

The release artifact is a PyInstaller onedir bundle named
`GalgameNewsToolbox`. Its executable is `GalgameNewsToolbox.exe` on Windows.

Install the pinned build tooling in a clean virtual environment, then run:

```powershell
python -m pip install -e ".[build]"
python packaging/build.py
```

The generated bundle is written below `dist/GalgameNewsToolbox/`. The spec
includes the repository `config/` and an optional repository `assets/`
directory. A release maintainer may place already-acquired `ffmpeg.exe` and
`ffprobe.exe` below `bin/`; the spec copies those files when present. The
build never downloads FFmpeg and no FFmpeg binaries are committed here.

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
stop and resume it, open the review page, and export reviewed media. Also check
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
