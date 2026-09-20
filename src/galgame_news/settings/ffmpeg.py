"""FFmpeg/ffprobe discovery with a diagnostic, dependency-free status."""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import shutil
import sys
from typing import Callable, Mapping


@dataclass(frozen=True)
class FFmpegStatus:
    available: bool
    source: str = "none"
    location: str | None = None
    ffmpeg_path: str | None = None
    ffprobe_path: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.available

    @property
    def ffmpeg(self) -> str | None:
        return self.ffmpeg_path

    @property
    def ffprobe(self) -> str | None:
        return self.ffprobe_path

    @property
    def diagnostic(self) -> str:
        if self.available:
            probe = self.ffprobe_path or "missing"
            return (
                f"FFmpeg available via {self.source}: {self.ffmpeg_path}; "
                f"ffprobe: {probe}"
            )
        return f"FFmpeg not found: {self.error or 'unknown error'}"


def _binary(directory: Path, stem: str) -> Path | None:
    for name in (f"{stem}.exe", stem):
        path = directory / name
        if path.is_file():
            return path.resolve()
    return None


def _from_location(raw: str | os.PathLike[str] | None, source: str) -> FFmpegStatus | None:
    if raw is None or not str(raw).strip():
        return None
    location = Path(os.path.expanduser(os.path.expandvars(str(raw).strip())))
    if location.is_file():
        if location.stem.casefold() in {"ffmpeg", "ffprobe"}:
            directory = location.parent
        else:
            return None
    else:
        directory = location
    ffmpeg = _binary(directory, "ffmpeg")
    if ffmpeg is None:
        return None
    ffprobe = _binary(directory, "ffprobe")
    return FFmpegStatus(
        available=True,
        source=source,
        location=str(directory.resolve()),
        ffmpeg_path=str(ffmpeg),
        ffprobe_path=str(ffprobe) if ffprobe else None,
        error=None if ffprobe else "ffprobe executable not found beside ffmpeg",
    )


def _bundle_roots(bundle_dir: Path | str | None) -> list[Path]:
    roots: list[Path] = []
    if bundle_dir is not None:
        selected = Path(bundle_dir).expanduser()
        roots.append(selected)
        if selected.name.casefold() == "bin":
            roots.append(selected.parent)
    else:
        frozen_root = getattr(sys, "_MEIPASS", None)
        if frozen_root:
            roots.append(Path(frozen_root))
        if getattr(sys, "frozen", False):
            executable = getattr(sys, "executable", None)
            if executable:
                roots.append(Path(executable).expanduser().resolve().parent)
        roots.append(Path(__file__).resolve().parents[3])
    return roots


def discover_ffmpeg(
    *,
    bundle_dir: Path | str | None = None,
    configured_location: Path | str | None = None,
    configured_probe_location: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
) -> FFmpegStatus:
    """Find FFmpeg in bundle, configured, or system locations in that order."""

    values = environ if environ is not None else os.environ
    find = which or shutil.which

    def with_configured_probe(status: FFmpegStatus) -> FFmpegStatus:
        if status.ffprobe_path is not None or configured_probe_location is None:
            return status
        probe = _from_location(configured_probe_location, "configured")
        if probe is None or probe.ffprobe_path is None:
            return status
        return replace(status, ffprobe_path=probe.ffprobe_path, error=None)

    for root in _bundle_roots(bundle_dir):
        bundled = _from_location(root / "bin", "bundled")
        if bundled is None and root.name.casefold() == "bin":
            bundled = _from_location(root, "bundled")
        if bundled is not None:
            if bundled.ffprobe_path is None:
                probe = find("ffprobe")
                if probe and Path(probe).is_file():
                    bundled = replace(bundled, ffprobe_path=str(Path(probe).resolve()), error=None)
            return bundled

    configured = str(values.get("FFMPEG_LOCATION") or "").strip() or (
        str(configured_location).strip() if configured_location is not None else ""
    )
    if configured:
        resolved = _from_location(configured, "configured")
        if resolved is not None:
            return with_configured_probe(resolved)

    ffmpeg = find("ffmpeg")
    if ffmpeg:
        resolved = _from_location(ffmpeg, "system")
        if resolved is not None:
            if resolved.ffprobe_path is None:
                probe = find("ffprobe")
                if probe and Path(probe).is_file():
                    resolved = replace(resolved, ffprobe_path=str(Path(probe).resolve()), error=None)
            return with_configured_probe(resolved)
        # ``which`` may return a bare executable whose sibling probe is not
        # present; still expose the executable path for diagnostics.
        path = Path(ffmpeg).expanduser().resolve()
        return FFmpegStatus(
            available=True,
            source="system",
            location=str(path.parent),
            ffmpeg_path=str(path),
            error="ffprobe executable not found beside ffmpeg",
        )

    return FFmpegStatus(available=False, error="FFmpeg executable not found")


def resolve_ffmpeg(
    config_location: Path | str | None = None,
    *,
    bundle_dir: Path | str | None = None,
    configured_probe_location: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
) -> FFmpegStatus:
    """Compatibility spelling for callers that use ``resolve_*`` naming."""

    return discover_ffmpeg(
        bundle_dir=bundle_dir,
        configured_location=config_location,
        configured_probe_location=configured_probe_location,
        environ=environ,
        which=which,
    )


resolve_ffmpeg_location = resolve_ffmpeg
