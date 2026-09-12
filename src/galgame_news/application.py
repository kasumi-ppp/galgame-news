"""Application orchestration for the v2 local pipeline."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from .config import load_config
from .curation import ImageCurator
from .discovery.adapters import DirectImageAdapter, OfficialHtmlAdapter, SteamAdapter, XAdapter
from .discovery.resolver import DefaultSourceResolver
from .domain import CollectionContext, FailureRecord, FailureStage, Issue, PipelineResult, SourceType
from .ingestion import DocxDocumentParser, OpenAINewsAnalyzer, RuleBasedNewsAnalyzer
from .delivery.output import OutputManager


class Application:
    def __init__(self, *, offline: bool = False, config_path=None, resolver=None, parser=None, analyzer=None, history=None):
        self.offline = offline
        self.config = load_config(config_path)
        self.parser = parser or DocxDocumentParser()
        self.analyzer = analyzer or RuleBasedNewsAnalyzer()
        self.resolver = resolver or DefaultSourceResolver()
        self.history = history

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
                    sources = [source for source in sources if source.source_type is SourceType.DIRECT_IMAGE or urlsplit(source.url).path.casefold().endswith((".jpg", ".jpeg", ".png", ".webp"))]
                for source in sources:
                    if source.source_type is SourceType.DIRECT_IMAGE or urlsplit(source.url).path.casefold().endswith((".jpg", ".jpeg", ".png", ".webp")):
                        adapter = DirectImageAdapter()
                    elif source.source_type is SourceType.STEAM:
                        adapter = SteamAdapter()
                    elif source.source_type is SourceType.OFFICIAL_X:
                        adapter = XAdapter()
                    else:
                        adapter = OfficialHtmlAdapter()
                    collection = adapter.collect(news, source, CollectionContext(now=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)))
                    candidates.extend(collection.candidates)
                    failures.extend(collection.failures)
            except Exception as exc:
                failures.append(FailureRecord(stage=FailureStage.RESOLVE, news_id=news.id, code="news_failed", message=str(exc), retryable=True))
        curated = ImageCurator(self.config).curate(issue, candidates, self.history)
        result = PipelineResult(issue=issue, candidates=curated.candidates, failures=failures)
        result.review_required = [candidate for candidate in result.candidates if candidate.review_reasons]
        OutputManager().write(result, output_dir)
        return result
