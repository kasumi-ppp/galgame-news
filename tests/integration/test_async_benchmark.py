"""Smoke coverage for the frozen subprocess benchmark and its report schema."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import pytest


PROJECT = Path(__file__).resolve().parents[2]
BENCH = PROJECT / "output" / "async_benchmark_20261002"


def test_local_benchmark_schema_and_outcome_parity():
    required = [BENCH / "baseline_src/galgame_news/pipeline/runner.py", BENCH / "baseline_config/default.toml"]
    if not all(path.is_file() for path in required):
        pytest.skip("历史异步基准的冻结源码和配置不在当前工作区；不能用当前代码替代旧版基线")
    completed = subprocess.run(
        [sys.executable, str(PROJECT / "scripts" / "benchmark_async_network.py"), "--mode", "local"],
        cwd=PROJECT, capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads((BENCH / "local" / "comparison.json").read_text(encoding="utf-8"))
    assert set(report) >= {"mode", "baseline", "async", "parity", "elapsed_improvement", "goal_30_percent_met"}
    assert report["mode"] == "local"
    assert report["parity"] == {
        "sources_equal": True, "images_equal": True,
        "successes_not_lower": True, "selected_not_lower": True,
    }
    for variant in ("baseline", "async"):
        result = report[variant]
        assert result["status"] == "completed"
        assert result["counts"]["downloadable"] >= 8
        assert any(image["duplicate_of"] and image["duplicate_kind"] == "perceptual_pixels"
                   for image in result["images"])
        assert all(image["original_file"]["sha256"] == image["sha256"]
                   for image in result["images"] if image["original_file"])
        assert all(value == 0 for key, value in result["file_checks"].items() if key != "downloadable")
        assert result["request_counts"]["total"] > 0
        assert result["stage_seconds"]["resolve"] > 0
        assert result["stage_seconds"]["collect"] > 0
        assert result["stage_seconds"]["download"] > 0
        assert Path(result["checkpoint"]).is_file()
