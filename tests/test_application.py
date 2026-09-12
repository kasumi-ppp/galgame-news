from pathlib import Path
from io import BytesIO

from PIL import Image

from galgame_news.domain import Issue, IssueDraft, NewsDraft, NewsItem, SourceRef, SourceType


def test_application_offline_runs_with_injected_components(tmp_path):
    from galgame_news.application import Application

    fixture = tmp_path / "sample.docx"
    fixture.write_bytes(b"not used")
    app = Application(offline=True)
    result = app.run(fixture, issue_id="1", output_dir=tmp_path / "out")
    assert result.issue.issue_id == "1"
    assert (tmp_path / "out" / "image_index.json").exists()


def test_application_isolates_news_failures(tmp_path):
    from galgame_news.application import Application

    class FailingResolver:
        def resolve(self, news):
            raise RuntimeError("one source failed")

    app = Application(offline=True, resolver=FailingResolver())
    fixture = tmp_path / "sample.docx"
    fixture.write_bytes(b"not used")
    result = app.run(fixture, issue_id="1", output_dir=tmp_path / "out")
    assert result.failures


def test_application_downloads_validates_hashes_and_saves_selected_images(tmp_path):
    from galgame_news.application import Application

    class Parser:
        def parse(self, path, issue_id):
            return IssueDraft(issue_id=issue_id, input_path=str(path), entries=[NewsDraft(sequence=1, section="新作", title="Game", body="CG")])

    class Analyzer:
        def analyze(self, draft):
            return Issue(issue_id=draft.issue_id, input_path=draft.input_path, news_items=[NewsItem(issue_id=draft.issue_id, sequence=1, section="新作", title="Game", body="CG")])

    class Resolver:
        def resolve(self, news):
            return [
                SourceRef(url="https://cdn.example/cg.jpg", domain="cdn.example", source_type=SourceType.DIRECT_IMAGE),
                SourceRef(url="https://cdn.example/logo.png", domain="cdn.example", source_type=SourceType.DIRECT_IMAGE),
            ]

    stream = BytesIO()
    Image.new("RGB", (800, 600), "red").save(stream, format="JPEG")
    jpeg = stream.getvalue()

    def transport(url, **_):
        return type("Response", (), {"content": jpeg, "headers": {"content-type": "image/jpeg"}, "status_code": 200, "url": url})()

    fixture = tmp_path / "sample.docx"
    fixture.write_bytes(b"fixture")
    result = Application(parser=Parser(), analyzer=Analyzer(), resolver=Resolver(), image_transport=transport).run(fixture, issue_id="259", output_dir=tmp_path / "out")
    assert len(result.candidates) == 1
    selected = result.candidates[0]
    assert selected.sha256 and selected.width == 800 and selected.height == 600
    assert selected.local_path and Path(selected.local_path).is_file()
    assert Path(selected.local_path).parent.name == "新作1"
