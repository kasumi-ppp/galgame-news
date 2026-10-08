"""Create a deterministic, unlabeled image review queue from an output run."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from PIL import Image


def _valid_review_file(output_dir: Path, path_value: str | None) -> bool:
    if not path_value:
        return False
    path = Path(path_value)
    if not path.is_absolute():
        path = output_dir / path
    if path.suffix.casefold() not in {".png", ".jpg", ".jpeg"} or not path.is_file():
        return False
    try:
        with Image.open(path) as image:
            if image.format not in {"PNG", "JPEG"}:
                return False
            image.verify()
        return True
    except Exception:
        return False


def _original_file_valid(output_dir: Path, candidate: dict) -> bool:
    path_value = candidate.get("original_path") or candidate.get("local_path")
    if not path_value:
        return False
    path = Path(path_value)
    if not path.is_absolute():
        path = output_dir / path
    if not path.is_file():
        return False
    expected = candidate.get("original_sha256") or candidate.get("sha256")
    if not expected:
        return True
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest() == expected


def build_manifest(output_dir: Path, limit: int | None = None) -> dict:
    index_path = output_dir / "image_index.json"
    if not index_path.is_file():
        raise FileNotFoundError(f"image index not found: {index_path}")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    news = {row["news_id"]: row for row in index.get("news_items", [])}
    candidates = index.get("candidates", [])
    grouped = defaultdict(list)
    for candidate in candidates:
        item = news.get(candidate.get("news_id"), {})
        image_path = candidate.get("local_path")
        review_valid = _valid_review_file(output_dir, image_path)
        actual_mime = candidate.get("output_mime_type") or candidate.get("mime_type")
        suffix = Path(image_path).suffix.casefold() if image_path else ""
        format_valid = review_valid and (
            (suffix == ".png" and actual_mime == "image/png")
            or (suffix in {".jpg", ".jpeg"} and actual_mime == "image/jpeg")
        )
        grouped[candidate.get("news_id", "")].append({
            "news_id": candidate.get("news_id"),
            "news_sequence": item.get("sequence"),
            "news_title": item.get("title", ""),
            "candidate_id": candidate.get("id"),
            "image_path": image_path,
            "original_path": candidate.get("original_path"),
            "review_file_valid": review_valid,
            "format_valid": format_valid,
            "original_file_valid": _original_file_valid(output_dir, candidate),
            "image_url": candidate.get("image_url", ""),
            "source_url": candidate.get("source_url", ""),
            "image_type_prediction": candidate.get("image_type", "unknown"),
            "is_game_cg_prediction": candidate.get("image_type") == "game_cg",
            "is_background_art_prediction": candidate.get("image_type") == "background_art",
            "is_decorative_prediction": candidate.get("image_type") == "decorative",
            "selected": bool(candidate.get("selected", False)),
            "curation_status": candidate.get("curation_status", "unselected"),
            "entity_match_prediction": candidate.get("signals", {}).get("entity_match"),
            "source_linked_prediction": candidate.get("signals", {}).get("source_linked"),
            "duplicate_of": candidate.get("signals", {}).get("duplicate_of"),
            "label": None,
            "entity_match_label": None,
            "source_linked_label": None,
            "image_type_label": None,
            "is_game_cg_label": None,
            "is_background_art_label": None,
            "is_decorative_label": None,
            "clarity_label": None,
            "duplicate_group_label": None,
            "fallback_usable_label": None,
            "annotator_note": "",
        })

    # Round-robin by news preserves representation when applying a sample cap.
    ordered = [row for news_id in sorted(grouped, key=lambda key: (
        grouped[key][0].get("news_sequence") is None,
        grouped[key][0].get("news_sequence") or 0,
        key,
    )) for row in sorted(grouped[news_id], key=lambda row: row["candidate_id"] or "")]
    if limit is not None:
        round_robin = []
        groups = [sorted(grouped[key], key=lambda row: row["candidate_id"] or "") for key in sorted(grouped)]
        while len(round_robin) < limit and any(groups):
            for group in groups:
                if group and len(round_robin) < limit:
                    round_robin.append(group.pop(0))
        ordered = round_robin

    return {
        "schema_version": 1,
        "issue_id": index.get("issue_id", output_dir.name),
        "source_output": str(output_dir.resolve()),
        "candidate_count": len(candidates),
        "news_count": len(news),
        "sample_count": len(ordered),
        "label_guide": {
            "A": "优质且与当前新闻相关", "B": "可能有用，需编辑判断",
            "C": "有效但基本不适合当前新闻", "D": "无效数据或损坏图片",
            "is_game_cg_label": "true/false，人工确认是否为当前作品实际游戏 CG",
            "is_background_art_label": "true/false，人工确认是否为作品背景美术，不能凭 bg 文件名标注",
            "is_decorative_label": "true/false，人工确认是否为栏目标题、分隔、控件或页面装饰",
            "clarity_label": "best/acceptable/blurry/unusable；同一 duplicate_group 中标出最清晰原图",
        },
        "samples": ordered,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path, help="pipeline output directory containing image_index.json")
    parser.add_argument("manifest", type=Path, help="destination JSON file")
    parser.add_argument("--limit", type=int, default=None, help="deterministic sample cap (default: all candidates)")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    payload = build_manifest(args.output_dir, args.limit)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {payload['sample_count']} candidates from {payload['news_count']} news items to {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
