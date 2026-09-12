from datetime import datetime, timezone
from pathlib import Path
import zipfile

import pytest

from galgame_news.domain import EventType, ImageNeed, NewsDraft
from galgame_news.ingestion.analyzer import OpenAINewsAnalyzer, RuleBasedNewsAnalyzer
from galgame_news.ingestion.docx_parser import DocxDocumentParser


def make_docx(path: Path, paragraphs: list[str]) -> None:
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f'<w:p><w:r><w:t>{text.replace("&", "&amp;").replace("<", "&lt;")}</w:t></w:r></w:p>' for text in paragraphs)
    document = f'<w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)


def test_docx_parser_keeps_all_news_sections_and_splits_concatenated_urls(tmp_path: Path):
    source = tmp_path / "weekly.docx"
    make_docx(
        source,
        [
            "目录",
            "新作",
            "《Happy Weekend》主机板制作完成#编辑#",
            "正文 https://official.example/news https://x.com/studio/status/1",
            "旧作",
            "《Old Game》更新#编辑#",
            "旧作正文",
        ],
    )

    issue = DocxDocumentParser().parse(source, "259")

    assert [entry.section for entry in issue.entries] == ["新作", "旧作"]
    assert issue.entries[0].title == "《Happy Weekend》主机板制作完成"
    assert issue.entries[0].source_urls == ["https://official.example/news", "https://x.com/studio/status/1"]
    assert issue.entries[1].title == "《Old Game》更新"


def test_docx_parser_ignores_preamble_and_url_fragments_as_headings(tmp_path: Path):
    source = tmp_path / "weekly.docx"
    make_docx(source, ["周报目录", "新作", "作品#作者#", "https://official.example/news#gallery"])

    issue = DocxDocumentParser().parse(source, "259")

    assert len(issue.entries) == 1
    assert issue.entries[0].title == "作品"
    assert issue.entries[0].source_urls == ["https://official.example/news#gallery"]


def test_rule_analyzer_extracts_entities_event_date_and_image_need():
    draft = type("Draft", (), {})()
    draft.issue_id = "259"
    draft.input_path = "input/259.docx"
    draft.published_at = None
    draft.entries = [
        NewsDraft(
            sequence=1,
            section="新作",
            title="《Happy Weekend》主机板制作完成",
            body="HOOKSOFT于2026年9月4日公布了两张CG，由原画綾瀬はづき绘制。",
            source_urls=["https://official.example/news"],
        )
    ]

    issue = RuleBasedNewsAnalyzer().analyze(draft)
    item = issue.news_items[0]

    assert item.game_names == ["Happy Weekend"]
    assert "HOOKSOFT" in item.organizations
    assert "綾瀬はづき" in item.people
    assert item.event_type is EventType.UPDATE
    assert item.event_at == datetime(2026, 9, 4, tzinfo=timezone.utc)
    assert item.image_need is ImageNeed.EXPLICIT_NEW_IMAGE
    assert item.keywords
    assert item.importance > 0


def test_rule_analyzer_does_not_treat_future_feature_cg_as_new_image():
    draft = type("Draft", (), {})()
    draft.issue_id = "259"
    draft.input_path = "weekly.docx"
    draft.published_at = None
    draft.entries = [
        NewsDraft(
            sequence=1,
            section="新作",
            title="《Future Game》发售日公布",
            body="预计于2026年11月发售，游戏包含20张CG。",
        )
    ]

    item = RuleBasedNewsAnalyzer().analyze(draft).news_items[0]

    assert item.event_type is EventType.RELEASE
    assert item.image_need is ImageNeed.UNKNOWN


def test_optional_llm_without_configuration_returns_rule_result_without_network():
    draft = type("Draft", (), {})()
    draft.issue_id = "259"
    draft.input_path = "weekly.docx"
    draft.published_at = None
    draft.entries = [NewsDraft(sequence=1, section="新作", title="《Game》更新", body="官方更新。")]

    fallback = RuleBasedNewsAnalyzer()
    result = OpenAINewsAnalyzer(fallback=fallback).analyze(draft)

    assert result.news_items[0].game_names == ["Game"]
    assert result.news_items[0].event_type is EventType.UPDATE
    assert result.news_items[0].id == fallback.analyze(draft).news_items[0].id


def test_optional_llm_invalid_structured_output_falls_back_and_records_failure():
    draft = type("Draft", (), {})()
    draft.issue_id = "259"
    draft.input_path = "weekly.docx"
    draft.published_at = None
    draft.entries = [NewsDraft(sequence=1, section="新作", title="《Game》更新", body="官方更新。")]

    analyzer = OpenAINewsAnalyzer(
        fallback=RuleBasedNewsAnalyzer(),
        provider="openai",
        model="test-model",
        api_key="test-key",
        invoker=lambda _: {"not": "a valid result"},
    )
    result = analyzer.analyze(draft)

    assert result.news_items[0].event_type is EventType.UPDATE
    assert analyzer.last_failures and analyzer.last_failures[0].code == "llm_invalid_output"
