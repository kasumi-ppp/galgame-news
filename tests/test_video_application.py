from __future__ import annotations

from pathlib import Path

from galgame_news.domain import (
    FailureStage,
    Issue,
    IssueDraft,
    NewsDraft,
    NewsItem,
    SourceRef,
    SourceType,
    VideoStatus,
)


def components():
    class Parser:
        def parse(self, path, issue_id):
            return IssueDraft(
                issue_id=issue_id,
                input_path=str(path),
                entries=[NewsDraft(sequence=1, section="新作", title="Video", body="")],
            )

    class Analyzer:
        def analyze(self, draft):
            return Issue(
                issue_id=draft.issue_id,
                input_path=draft.input_path,
                news_items=[
                    NewsItem(
                        issue_id=draft.issue_id,
                        sequence=1,
                        section="新作",
                        title="Video",
                        body="",
                    )
                ],
            )

    class Resolver:
        def resolve(self, news):
            return [
                SourceRef(
                    url="https://youtu.be/abc123",
                    domain="youtu.be",
                    source_type=SourceType.VIDEO,
                )
            ]

    return Parser(), Analyzer(), Resolver()


def test_no_videos_does_not_construct_downloader_and_writes_empty_index(tmp_path):
    from galgame_news.application import Application

    parser, analyzer, resolver = components()
    called = []

    def forbidden(**kwargs):
        called.append(True)
        raise AssertionError("video downloader must not be constructed")

    fixture = tmp_path / "fixture.docx"
    fixture.write_bytes(b"fixture")
    result = Application(
        parser=parser,
        analyzer=analyzer,
        resolver=resolver,
        no_videos=True,
        video_downloader_factory=forbidden,
    ).run(fixture, issue_id="1", output_dir=tmp_path / "out")

    assert called == []
    assert result.videos == []
    assert (tmp_path / "out" / "video_index.json").exists()
def test_application_downloads_discovered_video_and_isolates_downloader_failures(tmp_path):
    from galgame_news.application import Application

    parser, analyzer, resolver = components()
    calls = []

    class FakeDownloader:
        def __init__(self, config, **kwargs):
            calls.append(config)

        def download(self, candidates, output_dir):
            candidate = candidates[0].model_copy(update={
                "title": "PV",
                "status": VideoStatus.FAILED,
                "failure_reason": "fixture failure",
            })
            from galgame_news.domain import FailureRecord
            return [
                candidate
            ], [
                FailureRecord(
                    stage=FailureStage.DOWNLOAD,
                    news_id=candidate.news_id,
                    candidate_id=candidate.id,
                    code="fixture_failure",
                    message="fixture failure",
                    source_url=candidate.source_url,
                )
            ]

    fixture = tmp_path / "fixture.docx"
    fixture.write_bytes(b"fixture")
    result = Application(
        parser=parser,
        analyzer=analyzer,
        resolver=resolver,
        video_downloader_factory=FakeDownloader,
    ).run(fixture, issue_id="1", output_dir=tmp_path / "out")

    assert calls
    assert len(result.videos) == 1
    assert result.videos[0].status is VideoStatus.FAILED
    assert any(f.code == "fixture_failure" for f in result.failures)
    assert (tmp_path / "out" / "video_index.json").exists()
