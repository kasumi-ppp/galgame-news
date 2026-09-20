# Third-party notices

The toolbox is distributed with the following Python dependencies. Their
upstream licenses and notices remain applicable to each release:

- Pydantic — MIT
- Pillow — HPND / PIL license
- httpx — BSD-3-Clause
- Beautiful Soup 4 — MIT
- ddgs — MIT
- yt-dlp — Unlicense (with optional bundled components governed by their own notices)
- PySide6 (optional desktop extra) — LGPL-3.0/GPL-3.0, subject to the Qt distribution terms
- keyring (optional desktop extra) — MIT
- PyInstaller (optional build extra) — GPL-2.0-or-later with bootloader exception

The exact dependency versions used for a release are recorded in the pinned
build/development declarations. This repository does not include FFmpeg or
ffprobe binaries; when a release bundles locally acquired copies, retain the
corresponding upstream license and source notices alongside that release.
