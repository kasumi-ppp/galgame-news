"""Replay image curation from a finished task without network/API requests."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import html
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image, ImageDraw, ImageFont, ImageOps

from galgame_news.config import load_config
from galgame_news.curation.curator import ImageCurator
from galgame_news.delivery.asset_paths import recover_asset_path
from galgame_news.delivery.output import OutputManager
from galgame_news.desktop.i18n import reason_label
from galgame_news.domain import ImageCandidate, ImageCurationStatus, Issue, PipelineResult
from galgame_news.review.session import ReviewSession


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def state_counts(values):
    return {"candidates": len(values), "selected": sum(c.selected for c in values),
            "unselected": sum(not c.selected and c.curation_status is not ImageCurationStatus.INVALID for c in values),
            "invalid": sum(c.curation_status is ImageCurationStatus.INVALID for c in values)}


def contact_sheet(candidates, target, *, original=False):
    if not candidates:
        return
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 16) if font_path.is_file() else ImageFont.load_default()
    columns, cell_w, cell_h = 4, 400, 265
    sheet = Image.new("RGB", (columns * cell_w, ((len(candidates) + columns - 1) // columns) * cell_h), "#eeeeef")
    draw = ImageDraw.Draw(sheet)
    for index, c in enumerate(candidates):
        path = c.original_path if original else c.local_path
        if not path or not Path(path).is_file():
            continue
        x, y = index % columns * cell_w, index // columns * cell_h
        with Image.open(path) as image:
            image.seek(0)
            preview = ImageOps.contain(image.convert("RGB"), (cell_w - 12, 218))
        sheet.paste(preview, (x + 6, y + 6))
        name = Path(urlsplit(c.image_url).path).stem
        draw.text((x + 7, y + 227), f"{name}  {c.width}×{c.height}", font=font, fill="#222222")
        draw.text((x + 7, y + 247), f"{c.id} · {'已选' if c.selected else '待复核'}", font=font, fill="#555555")
    sheet.save(target)


def replay(task_root: Path, output_root: Path) -> dict:
    task_root = task_root.resolve()
    raw = task_root / "raw"
    if output_root.exists():
        raise ValueError("结果目录已存在，请选择新的目录")
    if output_root.resolve().is_relative_to(task_root):
        raise ValueError("不能把重筛结果写入原任务目录")
    old_index = read_json(raw / "image_index.json")
    checkpoint = read_json(task_root / "checkpoint.json")
    issue = Issue.model_validate(checkpoint["issue"])
    original_inputs = {str(p): digest(p) for p in (raw / "image_index.json", raw / "review_required.json", task_root / "checkpoint.json") if p.is_file()}
    candidates = [ImageCandidate.model_validate(c) for c in old_index["candidates"]]
    previous = [c.model_copy(deep=True) for c in candidates]
    original_hashes = {}
    restored, unavailable = 0, []
    for c in candidates:
        original = recover_asset_path(c.original_path, raw, c.original_sha256 or c.sha256)
        review = recover_asset_path(c.local_path, raw, c.output_sha256 or c.sha256)
        c.original_path = str(original) if original else None
        c.local_path = str(review) if review else None
        if original:
            original_hashes[original] = digest(original)
        source = original or review
        if source:
            try:
                with Image.open(source) as image:
                    image.seek(0)
                    image.load()
                restored += 1
            except (OSError, ValueError):
                c.signals["invalid_reason"] = "offline_asset_decode_failed"
        elif c.downloadable:
            c.signals["invalid_reason"] = "offline_asset_missing_or_hash_mismatch"
            c.downloadable = False
            unavailable.append(c.id)
    # Preserve prior failure records separately; curation is recomputed cleanly.
    output_root.mkdir(parents=True)
    config = load_config()
    config.visual_analysis.enabled = False
    print(f"已恢复 {restored} 个可解码图片记录，开始离线筛选", flush=True)
    curated = ImageCurator(config).curate(issue, candidates)
    result = PipelineResult(issue=issue, candidates=curated.candidates,
                            filtered_candidates=curated.filtered_candidates, failures=curated.failures)
    result.review_required = [c for c in result.all_candidates if c.review_reasons]
    OutputManager().write(result, output_root / "raw")
    print("筛选及审阅图输出完成，正在校验路径、编码和原件哈希", flush=True)

    saved = read_json(output_root / "raw" / "image_index.json")
    checks = Counter()
    for c in saved["candidates"]:
        for path_key, hash_key in (("local_path", "output_sha256"), ("original_path", "original_sha256")):
            if not c.get(path_key):
                continue
            path = Path(c[path_key])
            assert path.is_file() and path.resolve().is_relative_to(output_root.resolve()), c["id"]
            assert digest(path) == c[hash_key], (c["id"], path_key)
            checks[path_key] += 1
            if path_key == "local_path":
                with Image.open(path) as image:
                    image.load()
                    assert (image.format, path.suffix.lower(), c["output_mime_type"]) in {
                        ("PNG", ".png", "image/png"), ("JPEG", ".jpg", "image/jpeg")}
                checks["decodable_reviews"] += 1
        if c["selected"]:
            assert c.get("local_path"), c["id"]
        if c["downloadable"] and c["curation_status"] != "invalid":
            assert c.get("local_path"), c["id"]
    for path, expected in original_hashes.items():
        assert digest(path) == expected, str(path)
    for path, expected in original_inputs.items():
        assert digest(Path(path)) == expected, path
    checks["source_originals_unchanged"] = len(original_hashes)
    session = ReviewSession.from_output(output_root / "raw", task_root=output_root)
    assert len(session.images) == len(candidates)
    checks["review_imported"] = len(session.images)
    exported = session.export_final()
    checks["final_export_images"] = exported["image_count"]

    rows, differences = [], []
    old_by_id = {c.id: c for c in previous}
    for item in issue.news_items:
        before = [c for c in previous if c.news_id == item.id]
        after = [c for c in result.all_candidates if c.news_id == item.id]
        rows.append({"news_id": item.id, "title": item.title, "before": state_counts(before), "after": state_counts(after)})
        for c in after:
            prior = old_by_id[c.id]
            differences.append({"id": c.id, "news_id": item.id, "before_selected": prior.selected,
                "after_selected": c.selected, "before_type": prior.image_type.value,
                "after_type": c.image_type.value, "before_reasons": prior.selection_reasons,
                "after_reasons": c.selection_reasons, "source_url": c.source_url,
                "image_url": c.image_url, "local_path": c.local_path,
                "duplicate_of": c.signals.get("duplicate_of"),
                "gallery_evidence_from": c.signals.get("gallery_evidence_from")})
        selected = [c for c in after if c.selected]
        if selected:
            contact_sheet(selected, output_root / f"selected_{item.sequence:02d}.jpg")
    report = {"source_task": str(task_root), "offline": True, "api_requests": 0,
              "llm_requests": 0, "video_downloads": 0, "before": state_counts(previous),
              "after": state_counts(result.all_candidates), "checks": dict(checks),
              "unavailable_candidate_ids": unavailable, "news": rows}
    official_cg = [c for c in result.all_candidates if c.source_type.value == "official_site" and c.image_type.value == "game_cg"]
    report["cg_originals"] = {
        "selected_full_urls": [c.image_url for c in official_cg if c.selected and "/full/" in c.image_url],
        "unconfirmed_full_urls": [c.image_url for c in official_cg if not c.selected and "/full/" in c.image_url
                                  and "type_evidence_insufficient" in c.selection_reasons],
    }
    (output_root / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_root / "candidate_changes.json").write_text(json.dumps(differences, ensure_ascii=False, indent=2), encoding="utf-8")
    # Human report links only to local review copies and retained public source URLs.
    lines = ["# 262 图片离线重筛对照", "", "复用已有原件；网络、SocialData、LLM、视频请求均为 0。", "",
             "| 新闻 | 原入选 | 重筛入选 | 待复核 | 无效 |", "|---|---:|---:|---:|---:|"]
    for row in rows:
        lines.append(f"| {row['title']} | {row['before']['selected']} | {row['after']['selected']} | {row['after']['unselected']} | {row['after']['invalid']} |")
    lines += ["", "原任务及原件哈希已检查，未改动。三条原先下载批次失败的新闻没有可用原件，本次离线重筛无法补抓。", "",
              "[完整图片索引](raw/image_index.md) · [逐图原因对照](candidate_changes.json) · [检查统计](comparison.json)"]
    lines += ["", "## 本批旧数据的证据边界", "",
              "已确认的同画面变体采用原图优先。部分缩略图带滤色，PNG／WebP 同编号也可能是不同画面；文件名不能证明对应关系。", "",
              "旧索引未保留这些原图的 DOM 关系，无法通过本次像素核对恢复证据的原图继续留在待复核目录，未强制入选。新的官网采集器会保留明确 DOM 关系。", "",
              "### 仍需复核的原图"]
    for c in official_cg:
        if c.image_url in report["cg_originals"]["unconfirmed_full_urls"]:
            local = Path(c.local_path).relative_to(output_root).as_posix() if c.local_path else ""
            lines.append(f"- [审阅图]({local}) · [原图]({c.image_url}) · [官网]({c.source_url})")
    (output_root / "README.md").write_text("\n".join(lines), encoding="utf-8")
    sections = []
    for item in issue.news_items:
        selected = [c for c in result.all_candidates if c.news_id == item.id and c.selected]
        changed = [c for c in result.all_candidates if c.news_id == item.id and
                   (c.selected != old_by_id[c.id].selected or
                    (c.image_type.value == "game_cg" and c.source_type.value == "official_site"))]
        if not selected and not changed:
            continue
        cards = []
        for c in [*selected, *[c for c in changed if not c.selected]]:
            relative = Path(c.local_path).relative_to(output_root).as_posix() if c.local_path else ""
            prior = old_by_id[c.id]
            captions = "；".join(reason_label(r) for r in c.selection_reasons)
            cards.append(f'<article><a href="{html.escape(relative, quote=True)}"><img loading="lazy" src="{html.escape(relative, quote=True)}"></a>'
                f'<b>{"已选" if c.selected else "待复核"} · {c.width}×{c.height}</b>'
                f'<p>原状态：{"已选" if prior.selected else "未选"}<br>{html.escape(captions)}</p>'
                f'<a href="{html.escape(c.source_url, quote=True)}">官网／原帖</a> · '
                f'<a href="{html.escape(c.image_url, quote=True)}">原图</a></article>')
        sections.append(f'<h2>{html.escape(item.title)}</h2><div class="grid">{"".join(cards)}</div>')
    document = '<!doctype html><meta charset="utf-8"><title>图片离线重筛对照</title><style>body{background:#f5f3fa;color:#25212f;font-family:"Microsoft YaHei",sans-serif;margin:30px}a{color:#6755a3}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:18px}article{background:white;border-radius:12px;padding:12px}img{width:100%;height:220px;object-fit:contain;background:#ececf0}b{display:block;margin-top:8px}p{line-height:1.5}</style>'
    document += f'<h1>{html.escape(issue.issue_id)} 期图片离线重筛</h1><p>离线重筛：{report["before"]["selected"]} → {report["after"]["selected"]} 张入选。原图链接及重复版本保留。</p>' + "".join(sections)
    (output_root / "index.html").write_text(document, encoding="utf-8")
    return report


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", type=Path)
    parser.add_argument("--output", type=Path, default=Path("output") / ("262_cg_fix_" + datetime.now().strftime("%Y%m%d_%H%M%S")))
    args = parser.parse_args()
    print(f"新输出目录：{args.output.resolve()}", flush=True)
    print(json.dumps(replay(args.task, args.output.resolve()), ensure_ascii=False, indent=2))
