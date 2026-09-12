import json
import subprocess
import sys


def test_evaluate_reports_metrics_and_success(tmp_path):
    (tmp_path / "issue.json").write_text(json.dumps({"expected": [{"news_id": "n1", "candidate_id": "c1"}], "selected": [{"news_id": "n1", "candidate_id": "c1"}], "candidate_count": 2, "review_count": 1}), encoding="utf-8")
    result = subprocess.run([sys.executable, "scripts/evaluate.py", str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0
    assert "recall" in result.stdout


def test_live_smoke_requires_explicit_network_flag():
    result = subprocess.run([sys.executable, "scripts/live_smoke.py"], capture_output=True, text=True)
    assert result.returncode != 0
    assert "allow-network" in result.stderr
