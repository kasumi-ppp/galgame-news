"""Remove task internals only after a complete, verified image delivery."""

from __future__ import annotations

import json
import shutil
import stat
import uuid
from pathlib import Path

from PIL import Image


def _check_path(path: Path, root: Path) -> None:
    if not path.resolve().is_relative_to(root):
        raise ValueError("清理路径超出当前任务目录")
    info = path.lstat()
    if path.is_symlink() or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("任务目录含链接或联接点，保留内部数据")


def recover_interrupted_compaction(task_root: Path, *, task_id: str) -> bool:
    """Restore a directory switch interrupted before images were published."""
    from ..delivery.helpers import atomic_replace

    root = Path(task_root).absolute()
    if root.resolve() != root or (root / "checkpoint.json").exists() or (root / "final" / "images").is_dir():
        return False
    backups = []
    for path in root.parent.glob(f".cleanup-{root.name}-*"):
        try:
            _check_path(path, root.parent.resolve())
            payload = json.loads((path / "checkpoint.json").read_text(encoding="utf-8"))
            if payload.get("task_id") == task_id and payload.get("status") == "completed" and (path / "final" / "images").is_dir():
                backups.append(path)
        except (OSError, ValueError, TypeError):
            continue
    if len(backups) != 1:
        return False
    # Only discard the empty directories this switch could have created.
    if root.exists():
        _check_path(root, root.parent.resolve())
        if any(path.name != "final" for path in root.iterdir()):
            return False
        final = root / "final"
        if final.exists():
            _check_path(final, root)
            if not final.is_dir() or any(final.iterdir()):
                return False
            final.rmdir()
        root.rmdir()
    atomic_replace(backups[0], root)
    return True


def compact_image_output(task_root: Path, *, task_id: str) -> tuple[Path, str | None]:
    """Preflight all files before deleting anything; never touch other tasks."""
    from ..review.session import ReviewSession
    from ..delivery.helpers import atomic_replace
    from ..curation.download import ImageDownloader
    from ..domain import ImageCandidate

    root = Path(task_root).absolute()
    if root.resolve() != root:
        raise ValueError("任务清理目录不能包含链接")
    _check_path(root, root)
    checkpoint_path = root / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if checkpoint.get("status") != "completed" or checkpoint.get("task_id") != task_id:
        raise ValueError("只允许清理本任务已完成的输出")
    if any(failure.get("retryable") for failure in checkpoint.get("failures", [])) or checkpoint.get("retryable_candidate_ids"):
        raise ValueError("任务仍有异常或待恢复图片，保留恢复数据")
    news_ids = {item["id"] for item in checkpoint.get("issue", {}).get("news_items", [])}
    if not news_ids or set(checkpoint.get("completed_news_ids", [])) != news_ids:
        raise ValueError("本次所选新闻尚未全部处理完成，保留恢复数据")
    final = root / "final"
    images = final / "images"
    manifest = json.loads((final / "final_manifest.json").read_text(encoding="utf-8"))
    files = manifest.get("files")
    if not images.is_dir() or not isinstance(files, list):
        raise ValueError("缺少完整图片交付清单，保留内部数据")

    # The exporter may skip an undecodable asset. Do not delete its original.
    session = ReviewSession.from_output(root / "raw", task_root=root)
    candidates = [ImageCandidate.model_validate(value) for value in checkpoint.get("candidates", [])]
    if {value.id for value in candidates} != {value.id for value in session.images}:
        raise ValueError("检查点与图片索引的候选记录不一致，保留内部数据")
    for candidate in candidates:
        if candidate.download_status == "retryable_failed":
            raise ValueError("仍有需要恢复的图片下载")
        if candidate.download_status == "downloaded":
            path = candidate.original_path or candidate.local_path
            if not path or not Path(path).resolve().is_relative_to(root) or not ImageDownloader.reusable(candidate):
                raise ValueError("已下载原件缺失、哈希不符或不能解码，保留内部数据")
        elif candidate.download_status == "pending" and not candidate.signals.get("invalid_reason"):
            raise ValueError("图片下载尚未处理完成，保留内部数据")
    eligible = [candidate for candidate in [*session.accepted_images(), *session.pending_images()]
                if not session._hard_filtered(candidate)]
    expected = int(manifest.get("image_count", 0)) + int(manifest.get("pending_image_count", 0))
    if len(eligible) != expected:
        raise ValueError("部分已选或未候选图片尚未导出，保留内部数据")
    if len(files) != len(set(files)) or len(files) != expected + int(manifest.get("video_count", 0)):
        raise ValueError("交付清单数量不一致或存在重复记录，保留内部数据")
    delivered_selected = delivered_pending = 0
    for name in files:
        if not isinstance(name, str):
            raise ValueError("图片交付清单格式错误")
        path = final / name
        if not path.resolve().is_relative_to(images.resolve()) or not path.is_file():
            raise ValueError("交付文件缺失或超出图片目录")
        if path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            with Image.open(path) as image:
                actual = image.format
                image.verify()
            with Image.open(path) as image:
                image.load()
            if actual != ("PNG" if path.suffix.lower() == ".png" else "JPEG"):
                raise ValueError("交付图片编码与扩展名不一致")
            if "未候选" in path.relative_to(images).parts:
                delivered_pending += 1
            else:
                delivered_selected += 1
    if (delivered_selected != int(manifest.get("image_count", 0))
            or delivered_pending != int(manifest.get("pending_image_count", 0))):
        raise ValueError("已选与未候选图片数量不一致，保留内部数据")

    # Preflight every descendant, including Windows junctions, before removal.
    for path in root.rglob("*"):
        _check_path(path, root)
    known = {"raw", ".work", "logs", "retries", "media_state", "x_media_cache",
             "checkpoint.json", "request_options.json", "review_state.json",
             "task_state.sqlite3", "task_state.sqlite3-wal", "task_state.sqlite3-shm", "final"}
    if any(path.name not in known for path in root.iterdir()):
        raise ValueError("任务目录含额外文件，保留内部数据以免误删")
    if any(path.name not in {"images", "final_manifest.json"} for path in final.iterdir()):
        raise ValueError("交付目录含额外文件，保留内部数据以免误删")

    # First switch to a complete image-only task. A failed switch rolls back
    # all metadata and its original paths; deletion never leaves a live but
    # unusable checkpoint in the task directory.
    quarantine = root.parent / f".cleanup-{root.name}-{uuid.uuid4().hex}"
    if quarantine.exists() or quarantine.resolve().parent != root.parent.resolve():
        raise ValueError("内部清理目录不安全")
    atomic_replace(root, quarantine)
    try:
        root.mkdir()
        final.mkdir()
        atomic_replace(quarantine / "final" / "images", images)
    except BaseException:
        if images.exists():
            atomic_replace(images, quarantine / "final" / "images")
        if final.exists():
            final.rmdir()
        if root.exists():
            root.rmdir()
        atomic_replace(quarantine, root)
        raise
    try:
        _check_path(quarantine, root.parent.resolve())
        shutil.rmtree(quarantine)
    except OSError as exc:
        return images, f"图片目录已精简；旧内部文件被占用，待清理目录：{quarantine}（{type(exc).__name__}）"
    return images, None
