"""Evaluate human annotation fixtures without downloading images."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _evaluate_legacy(root: Path) -> dict:
    expected = selected = 0
    wrong = candidates = reviews = 0
    for path in sorted(root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        exp = {(row.get("news_id"), row.get("candidate_id")) for row in payload.get("expected", [])}
        got = {(row.get("news_id"), row.get("candidate_id")) for row in payload.get("selected", [])}
        expected += len(exp); selected += len(exp & got); wrong += len(got - exp)
        candidates += int(payload.get("candidate_count", len(got))); reviews += int(payload.get("review_count", 0))
    recall = selected / expected if expected else 1.0
    selected_total = selected + wrong
    error = wrong / selected_total if selected_total else 0.0
    if expected == 0:
        return {
            "status": "insufficient_annotations",
            "annotated_count": 0,
            "unlabeled_count": 0,
            "news_count": 0,
            "selected_count": 0,
            "selected_a_precision": None,
            "a_coverage": None,
            "bad_image_rate": None,
        }
    return {"recall": recall, "error_rate": error, "candidate_count": float(candidates), "review_count": float(reviews), "status": "legacy_unverified"}


def _ratio(numerator: int, denominator: int):
    return numerator / denominator if denominator else None


def _role_metrics(rows: list[dict], role: str) -> dict:
    label_key = f"is_{role}_label"
    typed = [row for row in rows if row.get(label_key) is not None or row.get("image_type_label") is not None]
    truth = [row for row in typed if row.get(label_key) is True or row.get("image_type_label") == role]
    predicted = [row for row in rows if row.get(f"is_{role}_prediction") is True or row.get("image_type_prediction") == role]
    selected = [row for row in predicted if row.get("selected") is True]
    labeled_selected = [row for row in selected if row in typed]
    selected_truth = [row for row in labeled_selected if row.get(label_key) is True or row.get("image_type_label") == role]
    retained = [row for row in truth if row.get("review_file_valid") is True and row.get("curation_status") in {"selected", "unselected"}]
    return {
        "labeled_count": len(typed),
        "true_count": len(truth),
        "predicted_count": len(predicted),
        "selected_count": len(selected),
        "labeled_selected_count": len(labeled_selected),
        "selected_precision": _ratio(len(selected_truth), len(labeled_selected)),
        "retention": _ratio(len(retained), len(truth)),
        "retained_count": len(retained),
    }


def evaluate(root: Path, *, rows_override: list[dict] | None = None) -> dict:
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(root.glob("*.json"))]
    manifests = [payload for payload in payloads if isinstance(payload, dict) and "samples" in payload]
    if rows_override is None and not manifests:
        return _evaluate_legacy(root)

    rows = rows_override if rows_override is not None else [row for manifest in manifests for row in manifest.get("samples", [])]
    valid_rows = [row for row in rows if row.get("label") in {"A", "B", "C", "D"}]
    unlabeled = len(rows) - len(valid_rows)
    selected = [row for row in valid_rows if row.get("selected") is True]
    selected_a = sum(row.get("label") == "A" for row in selected)
    selected_bad = sum(row.get("label") in {"C", "D"} for row in selected)
    all_a = sum(row.get("label") == "A" for row in valid_rows)
    recall = selected_a / all_a if all_a else None
    precision = selected_a / len(selected) if selected else None
    bad_rate = selected_bad / len(selected) if selected else None
    news_count = len({row.get("news_id") for row in valid_rows})
    enough = len(valid_rows) >= 300 and news_count >= 30 and unlabeled == 0
    metrics = {
        "status": "ready_for_acceptance" if enough else "insufficient_annotations",
        "annotated_count": len(valid_rows),
        "unlabeled_count": unlabeled,
        "news_count": news_count,
        "selected_count": len(selected),
        "selected_a_count": selected_a,
        "a_count": all_a,
        "selected_a_precision": precision,
        "a_coverage": recall,
        "bad_image_rate": bad_rate,
        "zero_selected_news_count": sum(
            not any(row.get("selected") is True for row in valid_rows if row.get("news_id") == news_id)
            for news_id in {row.get("news_id") for row in valid_rows}
        ),
        "per_news": {},
    }
    for news_id in sorted({row.get("news_id") for row in valid_rows}):
        group = [row for row in valid_rows if row.get("news_id") == news_id]
        group_a = sum(row.get("label") == "A" for row in group)
        group_selected = [row for row in group if row.get("selected") is True]
        group_selected_a = sum(row.get("label") == "A" for row in group_selected)
        metrics["per_news"][news_id] = {
            "annotated_count": len(group),
            "selected_count": len(group_selected),
            "a_count": group_a,
            "selected_a_count": group_selected_a,
            "a_coverage": group_selected_a / group_a if group_a else None,
        }
    selected_with_entity_label = [row for row in selected if row.get("entity_match_label") is not None]
    selected_with_source_label = [row for row in selected if row.get("source_linked_label") is not None]
    metrics["entity_precision"] = _ratio(sum(row.get("entity_match_label") is True for row in selected_with_entity_label), len(selected_with_entity_label))
    metrics["source_linked_precision"] = _ratio(sum(row.get("source_linked_label") is True for row in selected_with_source_label), len(selected_with_source_label))
    relevant = [row for row in valid_rows if row.get("label") in {"A", "B"}]
    retained = [row for row in relevant if row.get("curation_status", "selected" if row.get("selected") else "unselected") in {"selected", "unselected"} and row.get("review_file_valid") is True]
    metrics["valid_image_retention"] = len(retained) / len(relevant) if relevant else None
    metrics["valid_image_retained_count"] = len(retained)
    metrics["valid_image_count"] = len(relevant)
    metrics["reviewable_ab_retention"] = _ratio(len(retained), len(relevant))
    cg_rows = [row for row in valid_rows if row.get("is_game_cg_label") is not None]
    selected_cg = [row for row in selected if row.get("is_game_cg_prediction") is True or row.get("image_type_prediction") == "game_cg"]
    labeled_selected_cg = [row for row in selected_cg if row.get("is_game_cg_label") is not None]
    true_cg = [row for row in cg_rows if row.get("is_game_cg_label") is True]
    metrics["cg_selected_precision"] = _ratio(sum(row.get("is_game_cg_label") is True for row in labeled_selected_cg), len(labeled_selected_cg))
    metrics["cg_retention"] = _ratio(sum(row.get("review_file_valid") is True and row.get("curation_status") in {"selected", "unselected"} for row in true_cg), len(true_cg))
    metrics["background_art"] = _role_metrics(valid_rows, "background_art")
    metrics["decorative"] = _role_metrics(valid_rows, "decorative")
    clarity_groups: dict[str, list[dict]] = {}
    for row in valid_rows:
        group_id = row.get("duplicate_group_label")
        if group_id and row.get("clarity_label") is not None:
            clarity_groups.setdefault(str(group_id), []).append(row)
    clarity_hits = []
    for group in clarity_groups.values():
        best = [row for row in group if row.get("clarity_label") == "best"]
        if best:
            selected_best = any(row.get("selected") is True for row in best)
            selected_blurry = any(row.get("selected") is True and row.get("clarity_label") in {"blurry", "unusable"} for row in group)
            clarity_hits.append(selected_best and not selected_blurry)
    metrics["clarity_group_accuracy"] = _ratio(sum(clarity_hits), len(clarity_hits))
    reviewable_rows = [row for row in rows if row.get("review_file_valid") is True]
    metrics["format_validity_rate"] = _ratio(sum(row.get("format_valid") is True for row in reviewable_rows), len(reviewable_rows))
    original_rows = [row for row in rows if row.get("original_path") or row.get("original_file_valid") is not None]
    metrics["original_integrity_rate"] = _ratio(sum(row.get("original_file_valid") is True for row in original_rows), len(original_rows))
    metrics["a_coverage_gate"] = "report_only"
    if not enough:
        return metrics
    metrics["acceptance_targets_met"] = bool(
        precision is not None and precision >= 0.95
        and bad_rate is not None and bad_rate <= 0.02
        and metrics["entity_precision"] is not None and metrics["entity_precision"] >= 0.99
        and metrics["reviewable_ab_retention"] is not None and metrics["reviewable_ab_retention"] >= 0.99
    )
    return metrics


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("annotations", type=Path)
    args = parser.parse_args(argv)
    metrics = evaluate(args.annotations)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    if metrics["status"] == "insufficient_annotations":
        return 2
    if metrics["status"] == "legacy_unverified":
        return 0 if metrics["recall"] >= 0.85 and metrics["error_rate"] <= 0.05 else 1
    return 0 if metrics["acceptance_targets_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
