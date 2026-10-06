from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from PIL import Image

from galgame_news.delivery.asset_paths import recover_asset_path
from galgame_news.review import ReviewDecision, ReviewSession


def _fixture(tmp_path):
    raw = tmp_path / "task" / "raw"
    path = raw / "images" / "x1" / "one.png"
    path.parent.mkdir(parents=True)
    Image.new("RGB", (60, 40), "blue").save(path)
    original = raw / "originals" / "n1" / "one.source.png"
    original.parent.mkdir(parents=True)
    original.write_bytes(path.read_bytes())
    stage = raw.parent / ".work" / "publish-old"
    payload = {
        "id": "legacy-image", "news_id": "n1", "image_url": "https://example.test/a.png",
        "source_url": "https://example.test/game", "selected": True,
        "local_path": str(stage / path.relative_to(raw)),
        "original_path": str(stage / original.relative_to(raw)),
        "output_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "original_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
    }
    return raw, path, original, payload


def _load(raw, payload):
    index = raw / "image_index.json"
    index.write_text(json.dumps({"candidates": [payload]}), encoding="utf-8")
    before = index.read_bytes()
    session = ReviewSession.load(raw, state_path=raw.parent / "state.json")
    assert index.read_bytes() == before
    return session


def test_old_publish_paths_recover_both_assets_without_changing_raw_index(tmp_path):
    raw, path, original, payload = _fixture(tmp_path)
    session = _load(raw, payload)

    assert session.images[0].id == "legacy-image"
    assert session.images[0].local_path == str(path.resolve())
    assert session.images[0].original_path == str(original.resolve())
    assert session.decision("legacy-image") is ReviewDecision.ACCEPTED


@pytest.mark.parametrize("invalid", ["hash", "decode", "traversal", "wrong_task", "unknown_stage"])
def test_old_publish_paths_reject_unverified_recovery(tmp_path, invalid):
    raw, path, original, payload = _fixture(tmp_path)
    payload["original_path"] = None
    if invalid == "hash":
        payload["output_sha256"] = "0" * 64
    elif invalid == "decode":
        path.write_bytes(b"not an image")
        payload["output_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    elif invalid == "traversal":
        payload["local_path"] = str(raw.parent / ".work" / "publish-old" / "images" / ".." / "images" / "x1" / path.name)
    elif invalid == "wrong_task":
        payload["local_path"] = str(tmp_path / "other-task" / ".work" / "publish-old" / "images" / "x1" / path.name)
    else:
        payload["local_path"] = str(raw.parent / ".work" / "download-old" / "images" / "x1" / path.name)
    session = _load(raw, payload)

    assert session.images[0].local_path is None
    assert session.decision("legacy-image") is ReviewDecision.REJECTED
    assert session._source_paths == {}


def test_helper_rejects_symlink_escape(tmp_path):
    raw, path, original, payload = _fixture(tmp_path)
    external = tmp_path / "external.png"
    external.write_bytes(path.read_bytes())
    path.unlink()
    try:
        path.symlink_to(external)
    except OSError:
        pytest.skip("symlink creation unavailable")
    assert recover_asset_path(payload["local_path"], raw, payload["output_sha256"]) is None


def test_helper_checks_hash_for_current_and_recovered_paths(tmp_path):
    raw, path, original, payload = _fixture(tmp_path)
    assert recover_asset_path(payload["local_path"], raw, payload["output_sha256"]) == path.resolve()
    assert recover_asset_path(path, raw, payload["output_sha256"]) == path.resolve()
    assert recover_asset_path(path, raw, "0" * 64) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows path comparison is case insensitive")
def test_uppercase_work_stage_still_requires_image_decode(tmp_path):
    raw, path, original, payload = _fixture(tmp_path)
    path.write_bytes(b"not an image")
    payload["output_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload["local_path"] = str(raw.parent / ".WORK" / "publish-old" / path.relative_to(raw))
    payload["original_path"] = None
    # The hash matches and Windows can recover this path; image decoding must
    # still reject it, regardless of how the index spells the work directory.
    assert recover_asset_path(payload["local_path"], raw, payload["output_sha256"]) == path.resolve()

    session = _load(raw, payload)

    assert session.images[0].local_path is None
    assert session.decision("legacy-image") is ReviewDecision.REJECTED
    assert session._source_paths == {}


@pytest.mark.parametrize("location", ["external_task", "same_task", "relative"])
def test_uppercase_work_path_never_falls_back_to_existing_stage_file(tmp_path, location):
    raw, path, original, payload = _fixture(tmp_path)
    payload["original_path"] = None
    if location == "external_task":
        source = tmp_path / "other-task" / ".WORK" / "publish-old" / "one.png"
        recorded = str(source)
    elif location == "same_task":
        source = raw.parent / ".WORK" / "download-old" / "one.png"
        recorded = str(source)
    else:
        source = raw / ".WORK" / "publish-old" / "one.png"
        recorded = str(source.relative_to(raw))
    source.parent.mkdir(parents=True)
    source.write_bytes(path.read_bytes())
    payload["local_path"] = recorded
    assert recover_asset_path(recorded, raw, payload["output_sha256"]) is None

    session = _load(raw, payload)

    assert session.images[0].local_path is None
    assert session.decision("legacy-image") is ReviewDecision.REJECTED
    assert session._source_paths == {}
