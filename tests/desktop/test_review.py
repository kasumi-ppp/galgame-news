from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from galgame_news.desktop.review_page import ReviewPage
from galgame_news.review import ReviewDecision, ReviewSession


def _output(tmp_path: Path) -> tuple[Path, ReviewSession]:
    output = tmp_path / "output"
    output.mkdir()
    Image.new("RGB", (400, 400), "red").save(output / "one.jpg")
    (output / "image_index.json").write_text(
        json.dumps(
            {
                "issue_id": "issue-1",
                "news_items": [{"news_id": "news-1", "sequence": 1, "title": "News"}],
                "candidates": [
                    {
                        "id": "image-1",
                        "news_id": "news-1",
                        "image_url": "https://example.test/one.jpg",
                        "source_url": "https://example.test/source",
                        "local_path": "one.jpg",
                        "width": 400,
                        "height": 400,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    session = ReviewSession.from_output(output, state_path=tmp_path / "state.json")
    return output, session


def test_review_page_persists_decision_and_exposes_source(qtbot, tmp_path: Path):
    output, session = _output(tmp_path)
    page = ReviewPage()
    qtbot.addWidget(page)
    page.set_session(session)
    assert page.media_list.count() == 1
    page.media_list.setCurrentRow(0)
    page.accept_selected()
    assert session.decision("image-1") is ReviewDecision.ACCEPTED
    page.tabs.setCurrentIndex(0)
    page.media_list.setCurrentRow(0)
    assert page.source_button.isEnabled()
    assert page.retry_button.isEnabled()


def test_review_page_keeps_failed_candidates_in_session_order(qtbot, tmp_path: Path):
    output = tmp_path / "output"
    output.mkdir()
    candidates = []
    for index, score in (("b", 20), ("a", 80)):
        path = output / f"{index}.jpg"
        Image.new("RGB", (400, 400), "blue").save(path)
        candidates.append(
            {
                "id": f"image-{index}",
                "news_id": "news-1",
                "image_url": f"https://example.test/{index}.jpg",
                "source_url": "https://example.test/source",
                "local_path": path.name,
                "width": 400,
                "height": 400,
                "score": {"total": score, "relevance": score / 100},
            }
        )
    (output / "image_index.json").write_text(
        json.dumps({"candidates": candidates}), encoding="utf-8"
    )
    (output / "failed_items.json").write_text(
        json.dumps([{"candidate_id": "image-a"}, {"candidate_id": "image-b"}]),
        encoding="utf-8",
    )
    session = ReviewSession.from_output(output, state_path=tmp_path / "state.json")
    page = ReviewPage()
    qtbot.addWidget(page)
    page.set_session(session)
    assert [str(item.id) for item in page.failed_candidates()] == ["image-a", "image-b"]


def test_review_page_zoom_controls_change_scale(qtbot):
    page = ReviewPage()
    qtbot.addWidget(page)
    assert page.zoom_factor == 1.0
    page.zoom_in()
    page.zoom_out()
    assert page.zoom_factor == 1.0


def test_review_page_retry_exposes_optional_official_url(qtbot, tmp_path: Path):
    _output_dir, session = _output(tmp_path)
    page = ReviewPage()
    qtbot.addWidget(page)
    page.set_session(session)
    page.retry_url_edit.setText("https://official.example/retry")
    seen: list[str] = []
    page.retry_requested.connect(seen.append)

    page.retry_selected()

    assert seen == ["news-1"]
    assert page.retry_url() == "https://official.example/retry"
