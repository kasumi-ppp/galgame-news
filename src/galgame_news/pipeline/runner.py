"""Resumable orchestration engine shared by CLI and desktop frontends."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PureWindowsPath
from typing import Any, Callable
from urllib.parse import urlsplit

from ..config import PrescanConfig, load_config
from ..curation import ImageCurator, ImageDownloader
from ..delivery.output import OutputManager
from ..discovery.adapters import (
    DirectImageAdapter,
    DynamicPageAdapter,
    OfficialHtmlAdapter,
    SteamAdapter,
    VideoAdapter,
    XAdapter,
)
from ..discovery.resolver import DefaultSourceResolver
from ..domain import (
    CollectionContext,
    DiscoveryMethod,
    FailureRecord,
    FailureStage,
    ImageCandidate,
    Issue,
    NewsItem,
    NewsResult,
    PipelineResult,
    ReviewReason,
    SourceRef,
    SourceType,
    VideoCandidate,
    VideoStatus,
)
from ..ingestion import DocxDocumentParser, OpenAINewsAnalyzer, RuleBasedNewsAnalyzer
from .contracts import (
    CancellationRequested,
    CancellationToken,
    Checkpoint,
    ProgressEvent,
    TaskRequest,
    TaskResult,
)


class _NoNewsItemsError(ValueError):
    """Raised when analysis produces no actionable news entries."""


class PipelineRunner:
    """Run one task with isolated output and strict resumable checkpoints."""

    CHECKPOINT_SCHEMA_VERSION = 1
    _MAX_ISSUE_LABEL_LENGTH = 64
    _UNSAFE_TASK_LABEL_CHARS = frozenset('/\\<>:"|?*')

    def __init__(
        self,
        *,
        parser=None,
        analyzer=None,
        resolver=None,
        history=None,
        config: PrescanConfig | None = None,
        config_path=None,
        source_transport=None,
        image_transport=None,
        adapter_factory: Callable[..., Any] | None = None,
        video_downloader_factory: Callable[..., Any] | None = None,
        video_ffmpeg_detector=None,
        _legacy_output: bool = False,
    ) -> None:
        self.config = config or load_config(config_path)
        self.parser = parser or DocxDocumentParser()
        self.analyzer = analyzer
        self.history = history
        self.source_transport = source_transport
        self.image_transport = image_transport
        self.adapter_factory = adapter_factory
        self.video_downloader_factory = video_downloader_factory
        self.video_ffmpeg_detector = video_ffmpeg_detector
        self._legacy_output = _legacy_output
        self._resolver_injected = resolver is not None
        self.resolver = resolver or self._make_resolver(self.config)

    def _make_resolver(self, config):
        return DefaultSourceResolver(
            history_lookup=(self.history.sources_for if self.history else None),
            same_domain_depth=config.search.same_domain_depth,
            same_domain_transport=self.source_transport,
            search_max_results=config.search.max_results,
            search_timeout=config.network.timeout_seconds,
        )

    def run(
        self,
        request: TaskRequest,
        event_sink: Callable[[ProgressEvent], None] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> TaskResult:
        token = cancellation_token or CancellationToken()
        sink = event_sink or (lambda _event: None)
        failures: list[FailureRecord] = []
        resumed = bool(request.resume)
        task_id = request.task_id or ""
        task_dir = Path(request.output_dir)
        raw_dir = task_dir
        checkpoint_path = task_dir / "checkpoint.json"
        issue = self._empty_issue(request)
        candidates: list[ImageCandidate] = []
        videos: list[VideoCandidate] = []
        completed: set[str] = set()
        source_map: dict[str, list[SourceRef]] = {}
        input_hash = ""
        config_hash = ""

        try:
            config = self._config_for_request(request)
            self._activate_config(config)
            task_dir, raw_dir, checkpoint_path, task_id = self._task_paths(request)
            self._ensure_history(request)
            input_hash = self._sha256(request.input_path)
            config_hash = self._config_hash(config, request)
            self._prepare_task_dir(task_dir, request)
            if resumed:
                checkpoint = self._load_checkpoint(checkpoint_path)
                self._validate_checkpoint(checkpoint, request, input_hash, config_hash, task_id)
                issue = checkpoint.issue
                candidates = list(checkpoint.candidates)
                videos = list(checkpoint.videos)
                failures.extend(checkpoint.failures)
                completed = set(checkpoint.completed_news_ids)
                source_map = {key: list(value) for key, value in checkpoint.source_map.items()}
                if checkpoint.status == "completed":
                    if checkpoint.result is None:
                        raise ValueError("completed checkpoint has no result")
                    return TaskResult(
                        status="completed", task_id=task_id, task_dir=task_dir,
                        output_dir=raw_dir, checkpoint_path=checkpoint_path,
                        pipeline_result=checkpoint.result, resumed=True,
                    )
                if checkpoint.status == "failed":
                    result = checkpoint.result or self._pipeline_result(issue, candidates, videos, failures)
                    return TaskResult(
                        status="failed", task_id=task_id, task_dir=task_dir,
                        output_dir=raw_dir, checkpoint_path=checkpoint_path,
                        pipeline_result=result, resumed=True,
                    )
            elif request.task_dir is not None or request.checkpoint_path is not None:
                raise ValueError("task_dir/checkpoint_path are only valid when resuming")
        except Exception as exc:
            message = str(exc)
            if "input mismatch" in message:
                code = "checkpoint_input_mismatch"
            elif "config mismatch" in message:
                code = "checkpoint_config_mismatch"
            elif "schema mismatch" in message:
                code = "checkpoint_schema_mismatch"
            elif "issue mismatch" in message:
                code = "checkpoint_issue_mismatch"
            elif resumed and ("checkpoint" in message or "candidate news id" in message):
                code = "checkpoint_invalid"
            elif isinstance(exc, FileNotFoundError) and "configuration" not in message:
                code = "input_missing"
            else:
                code = "setup_error"
            failure = self._failure(code, message)
            if isinstance(exc, FileNotFoundError) and "configuration" not in message:
                failure = self._failure("input_missing", str(exc))
            failures.append(failure)
            result = self._pipeline_result(issue, candidates, videos, failures)
            return self._failed_result(
                request, task_id or request.task_id or request.issue_id,
                task_dir, raw_dir, checkpoint_path, result, failures, sink, resumed,
            )

        self._emit(sink, failures, self._event(
            "task_started", task_id, request.issue_id, message="pipeline started",
        ))
        try:
            token.wait_if_paused()
        except CancellationRequested:
            return self._cancel(
                request, task_id, task_dir, raw_dir, checkpoint_path, issue,
                input_hash, config_hash, completed, candidates, videos, failures,
                source_map, sink, resumed,
            )

        if not resumed:
            phase = "parse"
            try:
                token.wait_if_paused()
                self._emit(sink, failures, self._event(
                    "parse_started", task_id, request.issue_id, message="parsing DOCX",
                ))
                token.wait_if_paused()
                draft = self.parser.parse(request.input_path, request.issue_id)
                token.wait_if_paused()
                self._emit(sink, failures, self._event(
                    "parse_completed", task_id, request.issue_id, message="DOCX parsed",
                ))
                token.wait_if_paused()
                phase = "analyze"
                self._emit(sink, failures, self._event(
                    "analyze_started", task_id, request.issue_id, message="analyzing news",
                ))
                token.wait_if_paused()
                analyzer = self.analyzer
                if analyzer is None:
                    analyzer = (
                        OpenAINewsAnalyzer(
                            provider=request.llm_provider,
                            model=request.llm_model,
                            api_key=os.getenv("OPENAI_API_KEY"),
                        )
                        if request.llm_provider and request.llm_model
                        else RuleBasedNewsAnalyzer()
                    )
                issue = analyzer.analyze(draft)
                if not issue.news_items:
                    raise _NoNewsItemsError(
                        "No news items were recognized; check the DOCX section and title formatting"
                    )
                token.wait_if_paused()
                self._emit(sink, failures, self._event(
                    "analyze_completed", task_id, issue.issue_id, message="news analyzed",
                ))
                token.wait_if_paused()
                self._write_checkpoint(
                    checkpoint_path, status="running", task_id=task_id, issue=issue,
                    input_hash=input_hash, config_hash=config_hash,
                    completed_news_ids=completed, candidates=candidates, videos=videos,
                    failures=failures, source_map=source_map,
                )
            except CancellationRequested:
                return self._cancel(
                    request, task_id, task_dir, raw_dir, checkpoint_path, issue,
                    input_hash, config_hash, completed, candidates, videos, failures,
                    source_map, sink, resumed,
                )
            except _NoNewsItemsError as exc:
                self._emit(sink, failures, self._event(
                    f"{phase}_failed", task_id, request.issue_id, message=str(exc),
                ))
                failures.append(FailureRecord(
                    stage=FailureStage.ANALYZE, code="no_news_items",
                    message=str(exc), retryable=False,
                ))
                result = self._pipeline_result(issue, candidates, videos, failures)
                return self._failed_result(
                    request, task_id, task_dir, raw_dir, checkpoint_path, result,
                    failures, sink, resumed, input_hash, config_hash,
                )
            except Exception as exc:
                self._emit(sink, failures, self._event(
                    f"{phase}_failed", task_id, request.issue_id, message=str(exc),
                ))
                failures.append(self._failure(f"{phase}_error", str(exc)))
                result = self._pipeline_result(issue, candidates, videos, failures)
                return self._failed_result(
                    request, task_id, task_dir, raw_dir, checkpoint_path, result,
                    failures, sink, resumed, input_hash, config_hash,
                )

        total_news = len(issue.news_items)
        try:
            for news_index, news in enumerate(issue.news_items, start=1):
                token.wait_if_paused()
                if news.id in completed:
                    self._emit(sink, failures, self._event(
                        "news_skipped", task_id, issue.issue_id, news_id=news.id,
                        news_index=news_index, total_news=total_news,
                        message="restored from checkpoint",
                    ))
                    token.wait_if_paused()
                    continue
                self._emit(sink, failures, self._event(
                    "news_started", task_id, issue.issue_id, news_id=news.id,
                    news_index=news_index, total_news=total_news, message=news.title,
                ))
                token.wait_if_paused()
                news_sources: list[SourceRef] = []
                news_candidates: list[ImageCandidate] = []
                news_videos: list[VideoCandidate] = []
                self._emit(sink, failures, self._event(
                    "resolve_started", task_id, issue.issue_id, news_id=news.id,
                    news_index=news_index, total_news=total_news,
                    message="resolving sources",
                ))
                try:
                    token.wait_if_paused()
                    news_sources = self._filter_sources(
                        list(self._resolver_for(request).resolve(news)), request,
                    )
                    source_map[news.id] = news_sources
                    token.wait_if_paused()
                    self._emit(sink, failures, self._event(
                        "resolve_completed", task_id, issue.issue_id, news_id=news.id,
                        news_index=news_index, total_news=total_news,
                        message=f"{len(news_sources)} sources",
                    ))
                    token.wait_if_paused()
                except CancellationRequested:
                    raise
                except Exception as exc:
                    failures.append(FailureRecord(
                        stage=FailureStage.RESOLVE, news_id=news.id, code="news_failed",
                        message=str(exc), retryable=True,
                    ))
                    self._emit(sink, failures, self._event(
                        "resolve_failed", task_id, issue.issue_id, news_id=news.id,
                        news_index=news_index, total_news=total_news, message=str(exc),
                    ))

                for source in news_sources:
                    token.wait_if_paused()
                    self._emit(sink, failures, self._event(
                        "collect_started", task_id, issue.issue_id, news_id=news.id,
                        news_index=news_index, total_news=total_news, message=source.url,
                    ))
                    token.wait_if_paused()
                    try:
                        collection = self._collect(news, source)
                        news_candidates.extend(collection.candidates)
                        news_videos.extend(collection.video_candidates)
                        failures.extend(collection.failures)
                        for reason in collection.manual_review_reasons:
                            failures.append(FailureRecord(
                                stage=FailureStage.COLLECT, news_id=news.id,
                                code="manual_review_required", message=reason.value,
                                source_url=source.url, retryable=False,
                            ))
                        token.wait_if_paused()
                        self._emit(sink, failures, self._event(
                            "collect_completed", task_id, issue.issue_id,
                            news_id=news.id, news_index=news_index,
                            total_news=total_news, message=source.url,
                        ))
                        token.wait_if_paused()
                    except CancellationRequested:
                        raise
                    except Exception as exc:
                        failures.append(FailureRecord(
                            stage=FailureStage.COLLECT, news_id=news.id,
                            code="source_failed", message=str(exc),
                            source_url=source.url, retryable=True,
                        ))
                        self._emit(sink, failures, self._event(
                            "collect_failed", task_id, issue.issue_id, news_id=news.id,
                            news_index=news_index, total_news=total_news,
                            message=str(exc),
                        ))

                token.wait_if_paused()
                self._emit(sink, failures, self._event(
                    "download_started", task_id, issue.issue_id, news_id=news.id,
                    news_index=news_index, total_news=total_news,
                    message="media download",
                ))
                token.wait_if_paused()
                try:
                    if request.offline:
                        candidates.extend(news_candidates)
                        for video in news_videos:
                            self._skip_video(video, "offline_mode", failures)
                        token.wait_if_paused()
                    else:
                        accepted, download_failures = ImageDownloader(
                            self.config, transport=self.image_transport,
                        ).download(news_candidates, self._image_stage(task_dir))
                        candidates.extend(accepted)
                        failures.extend(download_failures)
                        # A single network request may finish while pause is
                        # requested; honor it immediately after the call.
                        token.wait_if_paused()
                        if request.no_videos or not self.config.video.enabled:
                            for video in news_videos:
                                self._skip_video(video, "video_download_disabled", failures)
                            videos.extend(news_videos)
                        elif news_videos:
                            token.wait_if_paused()
                            downloaded, video_failures = self._download_videos(
                                news_videos, self._video_stage(task_dir),
                            )
                            videos.extend(downloaded)
                            failures.extend(video_failures)
                            token.wait_if_paused()
                    videos = self._dedupe_videos([*videos, *news_videos])
                    token.wait_if_paused()
                    self._emit(sink, failures, self._event(
                        "download_completed", task_id, issue.issue_id, news_id=news.id,
                        news_index=news_index, total_news=total_news,
                        message="media downloaded",
                    ))
                    token.wait_if_paused()
                except CancellationRequested:
                    raise
                except Exception as exc:
                    failures.append(FailureRecord(
                        stage=FailureStage.DOWNLOAD, news_id=news.id,
                        code="download_failed", message=str(exc), retryable=True,
                    ))
                    self._emit(sink, failures, self._event(
                        "download_failed", task_id, issue.issue_id, news_id=news.id,
                        news_index=news_index, total_news=total_news, message=str(exc),
                    ))

                completed.add(news.id)
                self._write_checkpoint(
                    checkpoint_path, status="running", task_id=task_id, issue=issue,
                    input_hash=input_hash, config_hash=config_hash,
                    completed_news_ids=completed, candidates=candidates,
                    videos=videos, failures=failures, source_map=source_map,
                )
                self._emit(sink, failures, self._event(
                    "news_completed", task_id, issue.issue_id, news_id=news.id,
                    news_index=news_index, total_news=total_news,
                    message="news completed",
                ))
                token.wait_if_paused()

            token.wait_if_paused()
            self._emit(sink, failures, self._event(
                "curate_started", task_id, issue.issue_id, message="curating candidates",
            ))
            token.wait_if_paused()
            try:
                result = self._curate_result(
                    issue, candidates, failures, videos, source_map, request.max_images,
                )
            except CancellationRequested:
                raise
            except Exception as exc:
                failures.append(self._failure("curate_error", str(exc)))
                self._emit(sink, failures, self._event(
                    "curate_failed", task_id, issue.issue_id, message=str(exc),
                ))
                result = self._pipeline_result(issue, candidates, videos, failures)
                return self._failed_result(
                    request, task_id, task_dir, raw_dir, checkpoint_path, result,
                    failures, sink, resumed, input_hash, config_hash, completed, source_map,
                )
            self._emit(sink, failures, self._event(
                "curate_completed", task_id, issue.issue_id, message="candidates curated",
            ))
            token.wait_if_paused()
            self._emit(sink, failures, self._event(
                "output_started", task_id, issue.issue_id, message="publishing output",
            ))
            token.wait_if_paused()
            try:
                self._publish_output(result, raw_dir, task_dir)
                token.wait_if_paused()
            except CancellationRequested:
                raise
            except Exception as exc:
                failures.append(FailureRecord(
                    stage=FailureStage.OUTPUT, code="output_error",
                    message=str(exc), retryable=True,
                ))
                failure_index = len(failures) - 1
                self._emit(sink, failures, self._event(
                    "output_failed", task_id, issue.issue_id, message=str(exc),
                ))
                result.failures = self._merge_failures(
                    [*result.failures, *failures[failure_index:]]
                )
                return self._failed_result(
                    request, task_id, task_dir, raw_dir, checkpoint_path, result,
                    failures, sink, resumed, input_hash, config_hash, completed, source_map,
                )
            failure_index = len(failures)
            self._emit(sink, failures, self._event(
                "output_completed", task_id, issue.issue_id, message="output published",
            ))
            result.failures = self._merge_failures(
                [*result.failures, *failures[failure_index:]]
            )
            token.wait_if_paused()
            failure_index = len(failures)
            self._emit(sink, failures, self._event(
                "task_completed", task_id, issue.issue_id, message="pipeline completed",
            ))
            result.failures = self._merge_failures(
                [*result.failures, *failures[failure_index:]]
            )
            self._write_checkpoint(
                checkpoint_path, status="completed", task_id=task_id, issue=issue,
                input_hash=input_hash, config_hash=config_hash,
                completed_news_ids=completed, candidates=result.candidates,
                videos=result.videos, failures=result.failures,
                source_map=source_map, result=result,
            )
            shutil.rmtree(task_dir / ".work", ignore_errors=True)
            return TaskResult(
                status="completed", task_id=task_id, task_dir=task_dir,
                output_dir=raw_dir, checkpoint_path=checkpoint_path,
                pipeline_result=result, resumed=resumed,
            )
        except CancellationRequested:
            return self._cancel(
                request, task_id, task_dir, raw_dir, checkpoint_path, issue,
                input_hash, config_hash, completed, candidates, videos, failures,
                source_map, sink, resumed,
            )
        except Exception as exc:
            failures.append(FailureRecord(
                stage=FailureStage.OUTPUT, code="output_error", message=str(exc),
                retryable=True,
            ))
            self._emit(sink, failures, self._event(
                "output_failed", task_id, issue.issue_id, message=str(exc),
            ))
            result = self._pipeline_result(issue, candidates, videos, failures)
            return self._failed_result(
                request, task_id, task_dir, raw_dir, checkpoint_path, result,
                failures, sink, resumed, input_hash, config_hash, completed, source_map,
            )

    def retry_news(
        self,
        *,
        task_root: Path | str,
        issue_id: str,
        news_id: str,
        official_url: str,
        task_id: str | None = None,
        input_path: Path | str | None = None,
    ) -> str:
        """Collect one manually supplied official source into a new attempt.

        Retry output is deliberately separate from ``raw``.  The desktop
        controller reads the returned attempt through ``TaskStore`` and lets
        ``ReviewSession`` append only previously unseen stable IDs.
        """

        parsed = urlsplit(str(official_url).strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("official_url must be an absolute HTTP(S) URL")
        root = Path(task_root).expanduser().resolve()
        checkpoint_path = root / "checkpoint.json"
        issue = None
        if checkpoint_path.is_file():
            checkpoint = self._load_checkpoint(checkpoint_path)
            if checkpoint.issue_id != str(issue_id):
                raise ValueError("retry issue mismatch")
            issue = checkpoint.issue
        if issue is not None:
            news = next((item for item in issue.news_items if item.id == str(news_id)), None)
        else:
            news = None
        if news is None:
            # Imported legacy output may not have a checkpoint.  Keep this
            # fallback local to the retry seam; public domain contracts stay
            # unchanged and only the requested news item is materialized.
            news = NewsItem(
                issue_id=str(issue_id), sequence=1, section="新作",
                title=str(news_id), body="", source_urls=[str(official_url)],
            )
            object.__setattr__(news, "id", str(news_id))
        source = SourceRef(
            url=str(official_url),
            source_type=SourceType.OFFICIAL_SITE,
            domain=str(parsed.hostname or parsed.netloc),
            discovered_via=DiscoveryMethod.MANUAL,
            officiality=1.0,
        )
        collection = self._collect(news, source)
        failures = list(collection.failures)
        attempt_id = f"attempt-{uuid.uuid4().hex[:12]}"
        attempt_root = root / "retries" / str(news_id) / attempt_id
        attempt_root.mkdir(parents=True, exist_ok=False)
        accepted, download_failures = ImageDownloader(
            self.config, transport=self.image_transport,
        ).download(collection.candidates, attempt_root / "images")
        failures.extend(download_failures)
        image_payload = {
            "schema_version": 1,
            "issue_id": str(issue_id),
            "news_items": [{
                "news_id": str(news_id),
                "sequence": news.sequence,
                "section": news.section,
                "title": news.title,
                "candidates": [str(candidate.id) for candidate in accepted],
            }],
            "candidates": [candidate.model_dump(mode="json") for candidate in accepted],
            "failures": [failure.model_dump(mode="json") for failure in failures],
        }
        (attempt_root / "image_index.json").write_text(
            json.dumps(image_payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        (attempt_root / "video_index.json").write_text(
            json.dumps({"schema_version": 1, "issue_id": str(issue_id), "news_items": [], "videos": [], "failures": []}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (attempt_root / "failed_items.json").write_text(
            json.dumps([failure.model_dump(mode="json") for failure in failures], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return attempt_id

    def _config_for_request(self, request):
        return load_config(request.config_path) if request.config_path is not None else self.config

    def _activate_config(self, config):
        self.config = config
        if not self._resolver_injected:
            self.resolver = self._make_resolver(config)

    def _collect(self, news, source):
        if self.adapter_factory is not None:
            try:
                adapter = self.adapter_factory(news, source, self.config)
            except TypeError:
                adapter = self.adapter_factory(news, source)
        else:
            adapter = self._default_adapter(source)
        return adapter.collect(
            news, source,
            CollectionContext(
                timeout_seconds=self.config.network.timeout_seconds,
                max_candidates=self.config.search.max_candidates_per_source,
                now=datetime.now(timezone.utc),
            ),
        )

    def _default_adapter(self, source):
        if source.source_type is SourceType.VIDEO:
            return VideoAdapter()
        if ReviewReason.DYNAMIC_PAGE in source.review_reasons:
            return DynamicPageAdapter(transport=self.source_transport)
        path = urlsplit(source.url).path.casefold()
        if source.source_type is SourceType.DIRECT_IMAGE or path.endswith(
            (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")
        ):
            return DirectImageAdapter()
        if source.source_type is SourceType.STEAM:
            return SteamAdapter(transport=self.source_transport)
        if source.source_type is SourceType.OFFICIAL_X:
            return XAdapter(token=os.getenv("X_BEARER_TOKEN"), public_transport=self.source_transport)
        return OfficialHtmlAdapter(transport=self.source_transport)

    def _download_videos(self, candidates, output_dir):
        if self.video_downloader_factory is None:
            from ..video import VideoDownloader
            downloader = VideoDownloader(self.config.video, ffmpeg_detector=self.video_ffmpeg_detector)
        else:
            try:
                downloader = self.video_downloader_factory(
                    self.config.video, ffmpeg_detector=self.video_ffmpeg_detector,
                )
            except TypeError:
                downloader = self.video_downloader_factory(self.config.video)
        return downloader.download(candidates, output_dir)

    def _curate_result(self, issue, candidates, failures, videos, source_map, max_images):
        curated = ImageCurator(self.config).curate(issue, candidates, self.history)
        failures.extend(curated.failures)
        if max_images is not None:
            for news_id in {candidate.news_id for candidate in curated.candidates}:
                selected = [
                    candidate for candidate in curated.candidates
                    if candidate.news_id == news_id and candidate.selected
                ]
                for candidate in selected[max_images:]:
                    candidate.selected = False
        result = PipelineResult(
            issue=issue, candidates=curated.candidates,
            filtered_candidates=curated.filtered_candidates,
            failures=self._merge_failures(failures),
            videos=self._dedupe_videos(videos),
        )
        result.review_required = [c for c in result.all_candidates if c.review_reasons]
        if self.history is not None:
            for news in issue.news_items:
                try:
                    self.history.record_news_result(NewsResult(
                        news_item=news, sources=source_map.get(news.id, []),
                        candidates=[c for c in result.candidates if c.news_id == news.id],
                        failures=[f for f in result.failures if f.news_id == news.id],
                    ))
                except Exception as exc:
                    result.failures.append(FailureRecord(
                        stage=FailureStage.HISTORY, news_id=news.id,
                        code="history_error", message=str(exc), retryable=True,
                    ))
        result.failures = self._merge_failures(result.failures)
        return result

    def _publish_output(self, result, raw_dir, task_dir):
        if self._legacy_output:
            OutputManager().write(result, raw_dir)
            return
        work = task_dir / ".work"
        work.mkdir(parents=True, exist_ok=True)
        stage = work / f"publish-{uuid.uuid4().hex}"
        OutputManager().write(result, stage)
        backup = task_dir / f".raw-backup-{uuid.uuid4().hex}"
        try:
            if raw_dir.exists():
                os.replace(raw_dir, backup)
            os.replace(stage, raw_dir)
        except Exception:
            if not raw_dir.exists() and backup.exists():
                os.replace(backup, raw_dir)
            raise
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        self._rewrite_paths(result, stage, raw_dir)

    @staticmethod
    def _rewrite_paths(result, old_root, new_root):
        old_text, new_text = str(old_root), str(new_root)
        for candidate in result.all_candidates:
            if candidate.local_path:
                candidate.local_path = candidate.local_path.replace(old_text, new_text, 1)
        for video in result.videos:
            if video.local_path:
                video.local_path = video.local_path.replace(old_text, new_text, 1)

    def _cancel(
        self, request, task_id, task_dir, raw_dir, checkpoint_path, issue,
        input_hash, config_hash, completed, candidates, videos, failures,
        source_map, sink, resumed,
    ):
        try:
            self._write_checkpoint(
                checkpoint_path, status="cancelled", task_id=task_id, issue=issue,
                input_hash=input_hash, config_hash=config_hash,
                completed_news_ids=completed, candidates=candidates, videos=videos,
                failures=failures, source_map=source_map,
            )
        except Exception as exc:
            failures.append(self._failure("checkpoint_error", str(exc)))
        self._emit(sink, failures, self._event(
            "task_cancelled", task_id, issue.issue_id, message="cancelled",
        ))
        result = self._pipeline_result(issue, candidates, videos, failures)
        return TaskResult(
            status="cancelled", task_id=task_id, task_dir=task_dir,
            output_dir=raw_dir, checkpoint_path=checkpoint_path,
            pipeline_result=result, resumed=resumed,
        )

    def _failed_result(
        self, request, task_id, task_dir, raw_dir, checkpoint_path, result,
        failures, sink, resumed, input_hash="", config_hash="",
        completed=None, source_map=None,
    ):
        task_id = task_id or request.task_id or request.issue_id
        failure_index = len(failures)
        self._emit(sink, failures, self._event(
            "task_failed", task_id, request.issue_id, message="pipeline failed",
        ))
        if self._legacy_output:
            image_root = Path(raw_dir) / "images"
            preserve_existing_images = image_root.is_dir() and any(image_root.iterdir())
            if not preserve_existing_images:
                try:
                    OutputManager().write(result, raw_dir)
                except Exception as exc:
                    failures.append(self._failure("output_error", str(exc)))
        result.failures = self._merge_failures(
            [*result.failures, *failures[failure_index:]]
        )
        try:
            if input_hash and config_hash and checkpoint_path.parent.exists():
                self._write_checkpoint(
                    checkpoint_path, status="failed", task_id=task_id, issue=result.issue,
                    input_hash=input_hash, config_hash=config_hash,
                    completed_news_ids=completed or set(), candidates=result.candidates,
                    videos=result.videos, failures=result.failures,
                    source_map=source_map or {}, result=result,
                )
        except Exception:
            pass
        return TaskResult(
            status="failed", task_id=task_id, task_dir=task_dir,
            output_dir=raw_dir, checkpoint_path=checkpoint_path,
            pipeline_result=result, resumed=resumed,
        )

    def _pipeline_result(self, issue, candidates, videos, failures):
        result = PipelineResult(
            issue=issue, candidates=list(candidates),
            videos=self._dedupe_videos(videos),
            failures=self._merge_failures(failures),
        )
        result.review_required = [c for c in result.all_candidates if c.review_reasons]
        return result

    def _task_paths(self, request):
        output_root = Path(request.output_dir).resolve()
        if self._legacy_output:
            task_dir = output_root
            return task_dir, task_dir, task_dir / "checkpoint.json", request.task_id or request.issue_id
        task_label = self._safe_task_label(request.issue_id)
        if request.resume:
            if request.task_dir is None:
                raise ValueError("resume requires task_dir")
            task_dir = Path(request.task_dir).resolve()
            if not self._within(task_dir, output_root):
                raise ValueError("task_dir escapes output root")
            raw_dir = task_dir / "raw"
            checkpoint = task_dir / "checkpoint.json"
            if request.checkpoint_path is not None and Path(request.checkpoint_path).resolve() != checkpoint:
                raise ValueError("checkpoint_path must be task_dir/checkpoint.json")
            return task_dir, raw_dir, checkpoint, request.task_id or task_dir.name
        if request.task_dir is not None or request.checkpoint_path is not None:
            raise ValueError("arbitrary task paths are forbidden for new tasks")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        task_dir = output_root / f"{task_label}-{stamp}-{uuid.uuid4().hex[:8]}"
        return task_dir, task_dir / "raw", task_dir / "checkpoint.json", request.task_id or task_dir.name

    @classmethod
    def _safe_task_label(cls, issue_id: str) -> str:
        """Validate the issue id before using it as one directory component.

        The issue id remains unchanged in domain objects and published JSON;
        this label is only for the runner's timestamped task directory.
        """

        value = str(issue_id)
        label = value.strip()
        if not label or len(value) > cls._MAX_ISSUE_LABEL_LENGTH:
            raise ValueError("unsafe issue_id")
        if ".." in value or any(char in cls._UNSAFE_TASK_LABEL_CHARS for char in value):
            raise ValueError("unsafe issue_id")
        if Path(value).is_absolute() or PureWindowsPath(value).is_absolute():
            raise ValueError("unsafe issue_id")
        if any(unicodedata.category(char) == "Cc" for char in value):
            raise ValueError("unsafe issue_id")
        if label.endswith("."):
            raise ValueError("unsafe issue_id")
        return label

    @staticmethod
    def _within(path, root):
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _prepare_task_dir(self, task_dir, request):
        if request.resume:
            if not task_dir.is_dir():
                raise FileNotFoundError(f"task directory is missing: {task_dir}")
            return
        if self._legacy_output:
            task_dir.parent.mkdir(parents=True, exist_ok=True)
            task_dir.mkdir(parents=True, exist_ok=True)
            return
        task_dir.parent.mkdir(parents=True, exist_ok=True)
        task_dir.mkdir(exist_ok=False)
        (task_dir / ".work").mkdir()

    def _image_stage(self, task_dir):
        stage = task_dir / ".work" / "images"
        stage.mkdir(parents=True, exist_ok=True)
        return stage

    def _video_stage(self, task_dir):
        stage = task_dir / ".work" / "videos"
        stage.mkdir(parents=True, exist_ok=True)
        return stage

    def _resolver_for(self, request):
        if request.offline and not self._resolver_injected:
            return DefaultSourceResolver(
                history_lookup=(self.history.sources_for if self.history else None),
                same_domain_depth=0, same_domain_transport=self.source_transport,
                search_provider=lambda _news: [],
                search_max_results=self.config.search.max_results,
                search_timeout=self.config.network.timeout_seconds,
            )
        return self.resolver

    @staticmethod
    def _filter_sources(sources, request):
        if not request.offline:
            return list(sources)
        result = []
        for source in sources:
            path = urlsplit(source.url).path.casefold()
            if source.source_type in {SourceType.VIDEO, SourceType.DIRECT_IMAGE} or path.endswith(
                (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")
            ):
                result.append(source)
        return result

    def _ensure_history(self, request):
        if self.history is not None or request.history_db is None:
            return
        from ..delivery.history import SQLiteHistoryStore
        self.history = SQLiteHistoryStore(request.history_db)
        if not self._resolver_injected:
            self.resolver = self._make_resolver(self.config)

    @staticmethod
    def _empty_issue(request):
        return Issue(issue_id=request.issue_id, input_path=str(request.input_path), news_items=[])

    @staticmethod
    def _sha256(path):
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _config_hash(self, config=None, request=None):
        config = config or self.config
        behavior = {}
        if request is not None:
            behavior = {
                "offline": request.offline, "no_videos": request.no_videos,
                "max_images": request.max_images,
                "llm_provider": request.llm_provider, "llm_model": request.llm_model,
            }
        payload = {"config": config.model_dump(mode="json"), "request": behavior}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _write_checkpoint(
        self, path, *, status, task_id, issue, input_hash, config_hash,
        completed_news_ids, candidates, videos, failures, source_map, result=None,
    ):
        checkpoint = Checkpoint(
            schema_version=self.CHECKPOINT_SCHEMA_VERSION, status=status,
            task_id=task_id, issue_id=issue.issue_id, input_sha256=input_hash,
            config_sha256=config_hash, issue=issue,
            completed_news_ids=sorted(completed_news_ids),
            candidates=list(candidates), videos=self._dedupe_videos(videos),
            failures=self._merge_failures(failures), source_map=source_map,
            result=result,
        )
        self._validate_checkpoint_associations(checkpoint)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
        temporary.write_text(
            json.dumps(checkpoint.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def _load_checkpoint(self, path):
        return Checkpoint.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))

    def _validate_checkpoint(self, checkpoint, request, input_hash, config_hash, task_id):
        if checkpoint.schema_version != self.CHECKPOINT_SCHEMA_VERSION:
            raise ValueError("checkpoint schema mismatch")
        if checkpoint.issue_id != request.issue_id or checkpoint.issue.issue_id != request.issue_id:
            raise ValueError("checkpoint issue mismatch")
        if checkpoint.task_id != task_id:
            raise ValueError("checkpoint task mismatch")
        if checkpoint.input_sha256 != input_hash:
            raise ValueError("checkpoint input mismatch")
        if checkpoint.config_sha256 != config_hash:
            raise ValueError("checkpoint config mismatch")
        self._validate_checkpoint_associations(checkpoint)
        if checkpoint.status == "completed" and checkpoint.result is None:
            raise ValueError("completed checkpoint has no result")

    @staticmethod
    def _validate_checkpoint_associations(checkpoint):
        ids = {item.id for item in checkpoint.issue.news_items}
        if len(ids) != len(checkpoint.issue.news_items) or None in ids:
            raise ValueError("checkpoint issue has invalid news ids")
        if not set(checkpoint.completed_news_ids).issubset(ids):
            raise ValueError("checkpoint completed news id is unknown")
        if not set(checkpoint.source_map).issubset(ids):
            raise ValueError("checkpoint source map news id is unknown")
        for candidate in checkpoint.candidates:
            if candidate.news_id not in ids:
                raise ValueError("checkpoint candidate news id is unknown")
        for video in checkpoint.videos:
            if video.news_id not in ids:
                raise ValueError("checkpoint video news id is unknown")
        if checkpoint.result is not None and checkpoint.result.issue.issue_id != checkpoint.issue_id:
            raise ValueError("checkpoint result issue mismatch")

    @staticmethod
    def _dedupe_videos(videos):
        unique = {}
        for video in videos or []:
            unique.setdefault(video.id or video.video_url, video)
        return list(unique.values())

    @staticmethod
    def _merge_failures(failures):
        unique = {}
        for failure in failures or []:
            key = (
                failure.stage.value, failure.news_id, failure.candidate_id,
                failure.code, failure.message, failure.source_url,
            )
            if key not in unique:
                unique[key] = failure
            else:
                current = unique[key]
                occurrences = int(getattr(current, "occurrences", 1)) + int(
                    getattr(failure, "occurrences", 1)
                )
                unique[key] = current.model_copy(update={"occurrences": occurrences})
        return list(unique.values())

    @staticmethod
    def _skip_video(video, reason, failures):
        video.status = VideoStatus.SKIPPED
        video.failure_reason = reason
        if reason not in video.review_reasons:
            video.review_reasons.append(reason)
        failures.append(FailureRecord(
            stage=FailureStage.DOWNLOAD, news_id=video.news_id,
            candidate_id=video.id,
            code="offline_video_skip" if reason == "offline_mode" else reason,
            message=reason.replace("_", " "), source_url=video.source_url,
            retryable=False,
        ))

    @staticmethod
    def _failure(code, message):
        return FailureRecord(stage=FailureStage.PARSE, code=code, message=message, retryable=False)

    @staticmethod
    def _event(kind, task_id, issue_id, *, news_id=None, news_index=None, total_news=None, message=""):
        return ProgressEvent(
            kind=kind, task_id=task_id, issue_id=issue_id, news_id=news_id,
            news_index=news_index, total_news=total_news, message=message,
        )

    @staticmethod
    def _emit(sink, failures, event):
        try:
            sink(event)
        except Exception as exc:
            failures.append(FailureRecord(
                stage=FailureStage.OUTPUT, code="event_sink_failed",
                message=str(exc), retryable=False,
            ))
