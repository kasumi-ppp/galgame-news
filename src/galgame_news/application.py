"""Application orchestration for the v2 local pipeline."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .config import load_config
from .curation import ImageCurator, ImageDownloader
from .discovery.adapters import DirectImageAdapter, OfficialHtmlAdapter, SteamAdapter, XAdapter
from .discovery.resolver import DefaultSourceResolver
from .domain import CollectionContext, FailureRecord, FailureStage, Issue, PipelineResult, SourceType
from .ingestion import DocxDocumentParser, OpenAINewsAnalyzer, RuleBasedNewsAnalyzer
from .delivery.output import OutputManager
from .delivery.history import SQLiteHistoryStore


class Application:
    def __init__(self, *, offline: bool = False, config_path=None, resolver=None, parser=None, analyzer=None, history=None, history_db=None, max_images=None, llm_provider=None, llm_model=None, source_transport=None, image_transport=None):
        self.offline = offline
        self.config = load_config(config_path)
        self.parser = parser or DocxDocumentParser()
        self.analyzer = analyzer or (OpenAINewsAnalyzer(provider=llm_provider, model=llm_model, api_key=__import__("os").getenv("OPENAI_API_KEY")) if llm_provider and llm_model else RuleBasedNewsAnalyzer())
        self.history = history or (SQLiteHistoryStore(history_db) if history_db else None)
        self.resolver = resolver or DefaultSourceResolver(
            history_lookup=(self.history.sources_for if self.history else None),
            search_provider=(lambda _news: []) if offline else None,
            same_domain_depth=self.config.search.same_domain_depth,
            same_domain_transport=source_transport,
        )
        self.max_images = max_images
        self.source_transport = source_transport
        self.image_transport = image_transport

    def run(self, input_path: Path | str, *, issue_id: str, output_dir: Path | str) -> PipelineResult:
        failures: list[FailureRecord] = []
        try:
            draft = self.parser.parse(Path(input_path), issue_id)
            issue = self.analyzer.analyze(draft)
        except Exception as exc:
            failures.append(FailureRecord(stage=FailureStage.PARSE, code="parse_error", message=str(exc), retryable=False))
            issue = Issue(issue_id=issue_id, input_path=str(input_path), news_items=[])
        candidates = []
        for news in issue.news_items:
            try:
                sources = self.resolver.resolve(news)
                if self.offline:
                    sources = [source for source in sources if source.source_type is SourceType.DIRECT_IMAGE or urlsplit(source.url).path.casefold().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"))]
                for source in sources:
                    if source.source_type is SourceType.DIRECT_IMAGE or urlsplit(source.url).path.casefold().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")):
                        adapter = DirectImageAdapter()
                    elif source.source_type is SourceType.STEAM:
                        adapter = SteamAdapter(transport=self.source_transport)
                    elif source.source_type is SourceType.OFFICIAL_X:
                        adapter = XAdapter(token=os.getenv("X_BEARER_TOKEN"), public_transport=self.source_transport)
                    else:
                        adapter = OfficialHtmlAdapter(transport=self.source_transport)
                    collection = adapter.collect(
                        news,
                        source,
                        CollectionContext(
                            timeout_seconds=self.config.network.timeout_seconds,
                            max_candidates=self.config.search.max_candidates_per_source,
                            now=datetime.now(timezone.utc),
                        ),
                    )
                    candidates.extend(collection.candidates)
                    failures.extend(collection.failures)
            except Exception as exc:
                failures.append(FailureRecord(stage=FailureStage.RESOLVE, news_id=news.id, code="news_failed", message=str(exc), retryable=True))
        root = Path(output_dir)
        root.mkdir(parents=True, exist_ok=True)
        if self.offline:
            return self._curate_and_write(issue, candidates, failures, root)
        with tempfile.TemporaryDirectory(prefix=".image-stage-", dir=root) as staging:
            candidates, download_failures = ImageDownloader(self.config, transport=self.image_transport).download(candidates, staging)
            failures.extend(download_failures)
            return self._curate_and_write(issue, candidates, failures, root)

    def _curate_and_write(self, issue, candidates, failures, output_dir):
        curated = ImageCurator(self.config).curate(issue, candidates, self.history)
        if self.max_images is not None:
            for news_id in {candidate.news_id for candidate in curated.candidates}:
                selected = [candidate for candidate in curated.candidates if candidate.news_id == news_id and candidate.selected]
                for candidate in selected[self.max_images:]:
                    candidate.selected = False
        result = PipelineResult(issue=issue, candidates=curated.candidates, failures=failures)
        result.review_required = [candidate for candidate in result.candidates if candidate.review_reasons]
        OutputManager().write(result, output_dir)
        return result
