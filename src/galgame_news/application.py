"""Application orchestration for the v2 local pipeline."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .config import load_config
from .curation import ImageCurator, ImageDownloader
from .discovery.adapters import DirectImageAdapter, DynamicPageAdapter, OfficialHtmlAdapter, SteamAdapter, VideoAdapter, XAdapter
from .discovery.resolver import DefaultSourceResolver
from .domain import CollectionContext, FailureRecord, FailureStage, Issue, PipelineResult, ReviewReason, SourceType, VideoStatus
from .ingestion import DocxDocumentParser, OpenAINewsAnalyzer, RuleBasedNewsAnalyzer
from .delivery.output import OutputManager
from .delivery.history import SQLiteHistoryStore
from .pipeline import CancellationToken, PipelineRunner, TaskRequest


class Application:
    def __init__(self, *, offline: bool = False, no_videos: bool = False, config_path=None, resolver=None, parser=None, analyzer=None, history=None, history_db=None, max_images=None, llm_provider=None, llm_model=None, source_transport=None, image_transport=None, video_downloader_factory=None, video_ffmpeg_detector=None):
        self.offline = offline
        self.config = load_config(config_path)
        self.parser = parser or DocxDocumentParser()
        self.analyzer = analyzer or (OpenAINewsAnalyzer(provider=llm_provider, model=llm_model, api_key=__import__("os").getenv("OPENAI_API_KEY")) if llm_provider and llm_model else RuleBasedNewsAnalyzer())
        self.history = history or (SQLiteHistoryStore(history_db) if history_db else None)
        self.resolver = resolver or DefaultSourceResolver(
            history_lookup=(self.history.sources_for if self.history else None),
            search_provider=(lambda _news: []) if offline else None,
            same_domain_depth=0 if offline else self.config.search.same_domain_depth,
            same_domain_transport=source_transport,
            search_max_results=self.config.search.max_results,
            search_timeout=self.config.network.timeout_seconds,
        )
        self.max_images = max_images
        self.source_transport = source_transport
        self.image_transport = image_transport
        self.no_videos = no_videos
        self.video_downloader_factory = video_downloader_factory
        self.video_ffmpeg_detector = video_ffmpeg_detector

    def run(self, input_path: Path | str, *, issue_id: str, output_dir: Path | str) -> PipelineResult:
        def adapter_factory(news, source, config):
            path = urlsplit(source.url).path.casefold()
            if source.source_type is SourceType.VIDEO:
                return VideoAdapter()
            if ReviewReason.DYNAMIC_PAGE in source.review_reasons:
                return DynamicPageAdapter(transport=self.source_transport)
            if source.source_type is SourceType.DIRECT_IMAGE or path.endswith(
                (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")
            ):
                return DirectImageAdapter()
            if source.source_type is SourceType.STEAM:
                return SteamAdapter(transport=self.source_transport)
            if source.source_type is SourceType.OFFICIAL_X:
                return XAdapter(token=os.getenv("X_BEARER_TOKEN"), public_transport=self.source_transport)
            return OfficialHtmlAdapter(transport=self.source_transport)

        def progress(event):
            if event.kind == "resolve_started" and event.news_index is not None:
                display = event.message.encode("ascii", "replace").decode("ascii")
                print(
                    f"[galgame_news] resolving {event.news_index}/{event.total_news}: {display}",
                    flush=True,
                )
            elif event.kind == "resolve_completed" and event.news_index is not None:
                print(
                    f"[galgame_news] resolved {event.news_index}/{event.total_news}: {event.message}",
                    flush=True,
                )

        runner = PipelineRunner(
            parser=self.parser,
            analyzer=self.analyzer,
            resolver=self.resolver,
            history=self.history,
            config=self.config,
            source_transport=self.source_transport,
            image_transport=self.image_transport,
            adapter_factory=adapter_factory,
            video_downloader_factory=self.video_downloader_factory,
            video_ffmpeg_detector=self.video_ffmpeg_detector,
            _legacy_output=True,
        )
        task = TaskRequest(
            input_path=input_path,
            issue_id=issue_id,
            output_dir=output_dir,
            offline=self.offline,
            no_videos=self.no_videos,
            max_images=self.max_images,
            llm_provider=None,
            llm_model=None,
            task_id=f"legacy-{issue_id}",
        )
        result = runner.run(task, progress, CancellationToken())
        pipeline_result = result.pipeline_result
        if self.no_videos:
            pipeline_result.videos = []
        return pipeline_result

    def _download_videos(self, candidates, output_dir):
        if self.video_downloader_factory is None:
            from .video import VideoDownloader
            downloader = VideoDownloader(self.config.video, ffmpeg_detector=self.video_ffmpeg_detector)
        else:
            try:
                downloader = self.video_downloader_factory(self.config.video, ffmpeg_detector=self.video_ffmpeg_detector)
            except TypeError:
                downloader = self.video_downloader_factory(self.config.video)
        return downloader.download(candidates, output_dir)

    def _curate_and_write(self, issue, candidates, failures, output_dir, source_map=None, videos=None):
        curated = ImageCurator(self.config).curate(issue, candidates, self.history)
        failures.extend(curated.failures)
        if self.max_images is not None:
            for news_id in {candidate.news_id for candidate in curated.candidates}:
                selected = [candidate for candidate in curated.candidates if candidate.news_id == news_id and candidate.selected]
                for candidate in selected[self.max_images:]:
                    candidate.selected = False
        result = PipelineResult(issue=issue, candidates=curated.candidates, filtered_candidates=curated.filtered_candidates, failures=failures, videos=list(videos or []))
        result.review_required = [candidate for candidate in result.all_candidates if candidate.review_reasons]
        if self.history is not None:
            from .domain import NewsResult
            for news in issue.news_items:
                try:
                    self.history.record_news_result(NewsResult(
                        news_item=news,
                        sources=(source_map or {}).get(news.id, []),
                        candidates=[candidate for candidate in result.candidates if candidate.news_id == news.id],
                        failures=[failure for failure in result.failures if failure.news_id == news.id],
                    ))
                except Exception as exc:
                    result.failures.append(FailureRecord(stage=FailureStage.HISTORY, news_id=news.id, code="history_error", message=str(exc), retryable=True))
        OutputManager().write(result, output_dir)
        return result
