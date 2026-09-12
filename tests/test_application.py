from pathlib import Path


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
