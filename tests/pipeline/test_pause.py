from __future__ import annotations

import threading
import time

from galgame_news.domain import Issue, IssueDraft, NewsDraft, NewsItem
from galgame_news.pipeline import CancellationToken, PipelineRunner, TaskRequest


def test_cancellation_token_pause_blocks_until_resume():
    token = CancellationToken()
    token.pause()
    released = threading.Event()

    def waiter():
        token.wait_if_paused()
        released.set()

    thread = threading.Thread(target=waiter)
    thread.start()
    assert not released.wait(0.05)

    token.resume()
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert released.is_set()


def test_cancellation_token_cancel_wakes_a_paused_waiter():
    token = CancellationToken()
    token.pause()
    cancelled = threading.Event()

    def waiter():
        try:
            token.wait_if_paused()
        except Exception:
            cancelled.set()

    thread = threading.Thread(target=waiter)
    thread.start()
    assert not cancelled.wait(0.05)

    token.cancel()
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert cancelled.is_set()


def test_pipeline_runner_pause_holds_after_news_without_failure_and_resume_continues(tmp_path):
    class Parser:
        def parse(self, path, issue_id):
            return IssueDraft(
                issue_id=issue_id,
                input_path=str(path),
                entries=[
                    NewsDraft(sequence=index, section="新作", title=f"Game {index}", body="")
                    for index in range(1, 4)
                ],
            )

    class Analyzer:
        def analyze(self, draft):
            return Issue(
                issue_id=draft.issue_id,
                input_path=draft.input_path,
                news_items=[
                    NewsItem(
                        issue_id=draft.issue_id,
                        sequence=entry.sequence,
                        section=entry.section,
                        title=entry.title,
                        body=entry.body,
                    )
                    for entry in draft.entries
                ],
            )

    class Resolver:
        def resolve(self, news):
            return []

    input_path = tmp_path / "sample.docx"
    input_path.write_bytes(b"fixture")
    token = CancellationToken()
    events = []
    paused_after_first = threading.Event()

    def sink(event):
        events.append(event)
        if event.kind == "news_completed" and event.news_index == 1:
            token.pause()
            paused_after_first.set()

    runner = PipelineRunner(parser=Parser(), analyzer=Analyzer(), resolver=Resolver())
    result_holder = []
    thread = threading.Thread(
        target=lambda: result_holder.append(
            runner.run(
                TaskRequest(
                    input_path=input_path,
                    issue_id="pause-1",
                    output_dir=tmp_path / "out",
                    offline=True,
                ),
                sink,
                token,
            )
        )
    )
    thread.start()
    assert paused_after_first.wait(1)
    time.sleep(0.05)
    event_count_while_paused = len(events)
    time.sleep(0.05)
    assert len(events) == event_count_while_paused
    assert thread.is_alive()
    assert not result_holder

    token.resume()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result_holder[0].status == "completed"
    assert not result_holder[0].failures
    assert [event.news_index for event in events if event.kind == "news_completed"] == [1, 2, 3]


def test_pipeline_runner_stop_during_pause_exits_as_cancelled(tmp_path):
    class Parser:
        def parse(self, path, issue_id):
            return IssueDraft(
                issue_id=issue_id,
                input_path=str(path),
                entries=[NewsDraft(sequence=1, section="新作", title="Game", body="")],
            )

    class Analyzer:
        def analyze(self, draft):
            return Issue(
                issue_id=draft.issue_id,
                input_path=draft.input_path,
                news_items=[
                    NewsItem(
                        issue_id=draft.issue_id,
                        sequence=entry.sequence,
                        section=entry.section,
                        title=entry.title,
                        body=entry.body,
                    )
                    for entry in draft.entries
                ],
            )

    class Resolver:
        def resolve(self, news):
            return []

    input_path = tmp_path / "sample.docx"
    input_path.write_bytes(b"fixture")
    token = CancellationToken()
    token.pause()
    runner = PipelineRunner(parser=Parser(), analyzer=Analyzer(), resolver=Resolver())
    result_holder = []
    thread = threading.Thread(
        target=lambda: result_holder.append(
            runner.run(
                TaskRequest(
                    input_path=input_path,
                    issue_id="pause-stop-1",
                    output_dir=tmp_path / "out",
                    offline=True,
                ),
                cancellation_token=token,
            )
        )
    )
    thread.start()
    time.sleep(0.05)
    token.cancel()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result_holder[0].status == "cancelled"
