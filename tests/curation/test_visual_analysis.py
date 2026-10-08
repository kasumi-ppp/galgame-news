from pathlib import Path
from datetime import datetime, timezone

from galgame_news.config import VisualAnalysisConfig, load_config
from galgame_news.curation.visual_analysis import VisualAnalysisService
from galgame_news.curation.curator import ImageCurator
from galgame_news.domain import ImageCandidate, ImageNeed, Issue, NewsItem, SourceType


class FakeAnalyzer:
    def __init__(self):
        self.calls = []

    def analyze(self, paths):
        self.calls.append(tuple(paths))
        return [{"game_cg": 0.82, "goods": 0.03} for _ in paths]


def _candidate(image_path: Path):
    return ImageCandidate(
        news_id="n1", image_url="https://example.test/cg.png", source_url="https://example.test/news",
        local_path=str(image_path), fetched_at="2026-01-01T00:00:00Z",
    )


def test_visual_analysis_writes_shadow_signals_without_changing_selection(tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"fake image bytes")
    candidate = _candidate(image)
    before = candidate.model_dump(mode="json")

    VisualAnalysisService(FakeAnalyzer(), cache_path=tmp_path / "cache.json").annotate([candidate])

    assert candidate.image_type == "unknown"
    assert candidate.selected is False
    assert candidate.signals["visual_shadow_type"] == "game_cg"
    assert candidate.signals["visual_shadow_confidence"] == 0.82
    assert candidate.signals["visual_shadow_mode"] == "shadow"
    assert before["selected"] == candidate.selected


def test_visual_analysis_cache_avoids_reanalyzing_same_file(tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"same image")
    cache_path = tmp_path / "cache.json"
    first = FakeAnalyzer()
    VisualAnalysisService(first, cache_path=cache_path).annotate([_candidate(image)])
    second = FakeAnalyzer()
    candidate = _candidate(image)

    VisualAnalysisService(second, cache_path=cache_path).annotate([candidate])

    assert len(first.calls) == 1
    assert second.calls == []
    assert candidate.signals["visual_shadow_type"] == "game_cg"


def test_visual_analysis_failures_do_not_block_curated_candidates(tmp_path):
    image = tmp_path / "broken.png"
    image.write_bytes(b"broken image")

    class BrokenAnalyzer:
        def analyze(self, paths):
            raise RuntimeError("simulated out of memory")

    candidate = _candidate(image)
    result = VisualAnalysisService(BrokenAnalyzer(), cache_path=tmp_path / "cache.json").annotate([candidate])

    assert result.failed == 1
    assert "visual_shadow_type" not in candidate.signals
    assert candidate.selected is False


def test_visual_analysis_skips_candidates_without_local_files(tmp_path):
    candidate = _candidate(tmp_path / "missing.png")
    analyzer = FakeAnalyzer()

    result = VisualAnalysisService(analyzer, cache_path=tmp_path / "cache.json").annotate([candidate])

    assert result.analyzed == 0
    assert analyzer.calls == []


def test_curator_keeps_visual_prediction_shadow_only(tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"fake image")
    item = NewsItem(
        issue_id="i1", sequence=1, section="new", title="Game CG update", body="CG",
        game_names=["Game"], source_urls=["https://official.example/news"], image_need=ImageNeed.EXPLICIT_NEW_IMAGE,
    )
    config = load_config().model_copy(update={
        "visual_analysis": VisualAnalysisConfig(enabled=True, cache_path=str(tmp_path / "cache.json")),
    })

    def make_candidate():
        return ImageCandidate(
            news_id=item.id, image_url="https://official.example/gallery/cg01.jpg",
            source_url="https://official.example/news", source_type=SourceType.OFFICIAL_SITE,
            local_path=str(image), width=1280, height=720, downloadable=True,
            fetched_at=datetime.now(timezone.utc),
        )

    plain = make_candidate()
    shadow = make_candidate()
    ImageCurator(load_config()).curate(Issue(issue_id="i1", input_path="input.docx", news_items=[item]), [plain])
    ImageCurator(config, visual_analyzer=FakeAnalyzer()).curate(
        Issue(issue_id="i1", input_path="input.docx", news_items=[item]), [shadow],
    )

    assert shadow.image_type == plain.image_type
    assert shadow.selected == plain.selected
    assert shadow.signals["visual_shadow_type"] == "game_cg"
