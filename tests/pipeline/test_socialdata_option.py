from __future__ import annotations

import hashlib
import json

from galgame_news.domain import SourceRef, SourceType
from galgame_news.config import load_config, CONCURRENCY_FIELDS
from galgame_news.pipeline.contracts import TaskRequest
from galgame_news.pipeline.runner import PipelineRunner
from galgame_news.pipeline.checkpoint import config_hash


def test_task_socialdata_choice_is_passed_only_to_x_adapter(monkeypatch, tmp_path):
    import galgame_news.pipeline.runner as runner_module

    calls = []
    monkeypatch.setattr(runner_module, "create_x_adapter", lambda **kwargs: calls.append(kwargs) or object())
    runner = PipelineRunner(resolver=object())
    source = SourceRef(url="https://x.com/studio/status/123", domain="x.com", source_type=SourceType.OFFICIAL_X)
    request = TaskRequest(
        input_path=tmp_path / "261.docx", issue_id="261", output_dir=tmp_path / "out",
        use_socialdata_x=True,
    )

    runner._default_adapter(source, request=request)

    assert calls[0]["use_socialdata"] is True
    assert calls[0]["response_cache"] is runner._socialdata_response_cache


def test_unchecked_task_does_not_select_socialdata_transport(monkeypatch, tmp_path):
    import galgame_news.pipeline.runner as runner_module

    calls = []
    monkeypatch.setattr(runner_module, "create_x_adapter", lambda **kwargs: calls.append(kwargs) or object())
    runner = PipelineRunner(resolver=object())
    source = SourceRef(url="https://x.com/studio/status/123", domain="x.com", source_type=SourceType.OFFICIAL_X)
    request = TaskRequest(input_path=tmp_path / "261.docx", issue_id="261", output_dir=tmp_path / "out")

    runner._default_adapter(source, request=request)

    assert calls[0]["use_socialdata"] is False


def test_unchecked_socialdata_keeps_legacy_checkpoint_config_hash(tmp_path):
    config = load_config()
    request = TaskRequest(
        input_path=tmp_path / "261.docx", issue_id="261", output_dir=tmp_path / "out",
    )
    legacy_behavior = {
        "offline": request.offline,
        "no_videos": request.no_videos,
        "max_images": request.max_images,
        "llm_provider": request.llm_provider,
        "llm_model": request.llm_model,
    }
    legacy_config = config.model_dump(mode="json")
    for field in CONCURRENCY_FIELDS:
        legacy_config["network"].pop(field, None)
    legacy_config.pop("browser", None)
    legacy_config["selection"]["type_limits"].pop("decorative", None)
    for values in legacy_config["image_types"]["rejected_image_types_by_requirement"].values():
        if "decorative" in values:
            values.remove("decorative")
    encoded = json.dumps(
        {"config": legacy_config, "request": legacy_behavior},
        ensure_ascii=False, sort_keys=True, default=str,
    )

    assert config_hash(config, request) == hashlib.sha256(encoded.encode("utf-8")).hexdigest()
