import json
import subprocess
import sys


def test_evaluate_reports_metrics_and_success(tmp_path):
    (tmp_path / "issue.json").write_text(json.dumps({"expected": [{"news_id": "n1", "candidate_id": "c1"}], "selected": [{"news_id": "n1", "candidate_id": "c1"}], "candidate_count": 2, "review_count": 1}), encoding="utf-8")
    result = subprocess.run([sys.executable, "scripts/evaluate.py", str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0
    assert "recall" in result.stdout


def test_role_precision_excludes_unlabeled_predictions(tmp_path):
    import runpy
    evaluate = runpy.run_path("scripts/evaluate.py")["_role_metrics"]
    rows = [
        {"selected":True,"image_type_prediction":"background_art","image_type_label":"background_art","review_file_valid":True,"curation_status":"selected"},
        {"selected":True,"image_type_prediction":"background_art","image_type_label":None},
    ]
    metrics = evaluate(rows,"background_art")
    assert metrics["selected_count"] == 2
    assert metrics["labeled_selected_count"] == 1
    assert metrics["selected_precision"] == 1
    assert metrics["retention"] == 1
    assert evaluate(rows[1:],"background_art")["selected_precision"] is None


def test_evaluate_uses_a_d_labels_and_does_not_score_unlabeled_rows(tmp_path):
    payload = {
        "schema_version": 1,
        "samples": [
            {"news_id": "n1", "candidate_id": "a", "selected": True, "label": "A"},
            {"news_id": "n1", "candidate_id": "b", "selected": True, "label": "C"},
            {"news_id": "n2", "candidate_id": "c", "selected": False, "label": "A"},
            {"news_id": "n2", "candidate_id": "d", "selected": False, "label": None},
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    result = subprocess.run([sys.executable, "scripts/evaluate.py", str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 2
    metrics = json.loads(result.stdout)
    assert metrics["selected_a_precision"] == 0.5
    assert metrics["a_coverage"] == 0.5
    assert metrics["bad_image_rate"] == 0.5
    assert metrics["unlabeled_count"] == 1
    assert metrics["status"] == "insufficient_annotations"


def test_evaluate_does_not_treat_empty_annotations_as_perfect_recall(tmp_path):
    (tmp_path / "empty.json").write_text(json.dumps({"samples": []}), encoding="utf-8")
    result = subprocess.run([sys.executable, "scripts/evaluate.py", str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "insufficient_annotations"


def test_evaluate_empty_directory_is_not_a_perfect_legacy_score(tmp_path):
    result = subprocess.run([sys.executable, "scripts/evaluate.py", str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "insufficient_annotations"


def test_evaluate_ignores_non_manifest_json_in_output_directory(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"samples": []}), encoding="utf-8")
    (tmp_path / "review_required.json").write_text(json.dumps([{"id": "candidate"}]), encoding="utf-8")
    result = subprocess.run([sys.executable, "scripts/evaluate.py", str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 2
    assert json.loads(result.stdout)["unlabeled_count"] == 0


def test_build_annotation_manifest_is_deterministic_and_leaves_labels_blank(tmp_path):
    out = tmp_path / "run"
    out.mkdir()
    index = {
        "issue_id": "i1",
        "news_items": [{"news_id": "n1", "sequence": 1, "title": "news", "candidates": ["c1", "c2"]}],
        "candidates": [
            {"id": "c1", "news_id": "n1", "local_path": "c1.png", "selected": True, "image_type": "game_cg"},
            {"id": "c2", "news_id": "n1", "local_path": None, "selected": False, "image_type": "unknown"},
        ],
    }
    (out / "image_index.json").write_text(json.dumps(index), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "scripts/build_annotation_manifest.py", str(out), str(tmp_path / "manifest.json")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    rows = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))["samples"]
    assert [row["candidate_id"] for row in rows] == ["c1", "c2"]
    assert all(row["label"] is None and row["entity_match_label"] is None for row in rows)
    assert all(row["is_game_cg_label"] is None and row["clarity_label"] is None for row in rows)
    assert rows[0]["selected"] is True


def test_evaluator_reports_cg_clarity_and_reviewable_file_metrics_without_coverage_gate(tmp_path):
    import runpy
    evaluate = runpy.run_path("scripts/evaluate.py")["evaluate"]

    rows = [
        {
            "news_id": "n1", "candidate_id": "c1", "selected": True,
            "label": "A", "entity_match_label": True, "source_linked_label": True,
            "image_type_prediction": "game_cg", "is_game_cg_label": True,
            "clarity_label": "best", "curation_status": "selected",
            "image_path": None, "review_file_valid": True, "format_valid": True,
            "original_file_valid": True, "duplicate_group_label": "g1",
        },
        {
            "news_id": "n1", "candidate_id": "c2", "selected": False,
            "label": "B", "entity_match_label": True, "source_linked_label": True,
            "image_type_prediction": "game_cg", "is_game_cg_label": True,
            "clarity_label": "blurry", "curation_status": "unselected",
            "image_path": None, "review_file_valid": True, "format_valid": True,
            "original_file_valid": True, "duplicate_group_label": "g1",
        },
    ]
    metrics = evaluate(tmp_path, rows_override=rows)
    assert metrics["entity_precision"] == 1.0
    assert metrics["cg_selected_precision"] == 1.0
    assert metrics["cg_retention"] == 1.0
    assert metrics["reviewable_ab_retention"] == 1.0
    assert metrics["format_validity_rate"] == 1.0
    assert metrics["original_integrity_rate"] == 1.0
    assert metrics["a_coverage_gate"] == "report_only"
    assert metrics["clarity_group_accuracy"] == 1.0
