"""Evaluate human annotation fixtures without downloading images."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def evaluate(root: Path) -> dict[str, float]:
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
    return {"recall": recall, "error_rate": error, "candidate_count": float(candidates), "review_count": float(reviews)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("annotations", type=Path)
    args = parser.parse_args(argv)
    metrics = evaluate(args.annotations)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0 if metrics["recall"] >= 0.85 and metrics["error_rate"] <= 0.05 else 1


if __name__ == "__main__":
    raise SystemExit(main())
