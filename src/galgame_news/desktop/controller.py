"""Desktop orchestration facade and Qt thread boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot

from ..pipeline import CancellationToken, PipelineRunner, ProgressEvent, TaskRequest
from ..review import ReviewSession
from ..settings import CredentialStore, InMemoryCredentialBackend, SettingsStore
from ..tasks import TaskRecord, TaskStore
from .history_page import HistoryPage
from .new_task_page import NewTaskPage
from .progress_page import ProgressPage
from .review_page import ReviewPage
from .settings_page import SettingsPage
from .theme import apply_theme
from .worker import PipelineWorker


class DesktopController(QObject):
    """Own one optional pipeline worker and the five page models/widgets."""

    task_started = Signal(object)
    progress_event = Signal(object)
    task_finished = Signal(object)
    task_cancelled = Signal(object)
    task_failed = Signal(object)
    task_paused = Signal()
    task_resumed = Signal()
    stop_requested = Signal()
    review_loaded = Signal(object)
    retry_finished = Signal(object)
    retry_failed = Signal(object)
    history_changed = Signal()

    # Camel-case aliases are useful to Qt-oriented callers while snake-case is
    # the normal Python spelling.
    taskStarted = Signal(object)
    progressEvent = Signal(object)
    taskFinished = Signal(object)
    taskCancelled = Signal(object)
    taskFailed = Signal(object)

    def __init__(
        self,
        *,
        runner_factory: Callable[[], Any] | None = None,
        task_store: TaskStore | None = None,
        settings_store: SettingsStore | None = None,
        credential_store: CredentialStore | None = None,
        app_data: Path | str | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.runner_factory = runner_factory or (lambda: PipelineRunner())
        self.task_store = task_store or TaskStore(app_data)
        self.settings_store = settings_store or SettingsStore(app_data=app_data)
        if credential_store is None:
            try:
                credential_store = CredentialStore()
            except RuntimeError:
                credential_store = CredentialStore(InMemoryCredentialBackend())
        self.credential_store = credential_store
        settings = self.settings_store.load() if self.settings_store.path.exists() else None
        default_output = settings.default_output_path if settings is not None else None

        self.new_task_page = NewTaskPage(default_output)
        self.progress_page = ProgressPage()
        self.review_page = ReviewPage()
        self.history_page = HistoryPage(self.task_store)
        self.settings_page = SettingsPage(self.settings_store, self.credential_store)
        self.new_task_page.start_requested.connect(self._start_from_page)
        self.new_task_page.import_requested.connect(self.import_output)
        self.progress_page.pause_requested.connect(self.pause_task)
        self.progress_page.resume_requested.connect(self.resume_current_task)
        self.progress_page.stop_requested.connect(self.cancel_task)
        self.history_page.review_requested.connect(self.load_review)
        self.history_page.resume_requested.connect(self.resume_task)
        self.review_page.retry_requested.connect(self.retry_news)
        self.settings_page.theme_changed.connect(self.apply_theme)

        self._thread: QThread | None = None
        self._worker: PipelineWorker | None = None
        self._token: CancellationToken | None = None
        self.current_request: TaskRequest | None = None
        self.current_record: TaskRecord | None = None
        self.review_record: TaskRecord | None = None
        self.last_result: Any = None
        self.last_error: BaseException | None = None

    @property
    def worker(self) -> PipelineWorker | None:
        return self._worker

    @property
    def thread(self) -> QThread | None:
        return self._thread

    @property
    def is_running(self) -> bool:
        return bool(self._thread is not None and self._thread.isRunning())

    @property
    def is_paused(self) -> bool:
        return bool(self._token is not None and self._token.is_paused)

    def _start_from_page(self, payload: object) -> bool:
        values = dict(payload) if isinstance(payload, dict) else {}
        return self.start_task(**values)

    def start_task(
        self,
        request: TaskRequest | None = None,
        *,
        input_path: Path | str | None = None,
        issue_id: str | None = None,
        output_dir: Path | str | None = None,
        offline: bool = False,
        no_videos: bool = False,
        resume: bool = False,
        task_dir: Path | str | None = None,
        task_id: str | None = None,
    ) -> bool:
        if self.is_running:
            return False
        if request is None:
            if input_path is None or issue_id is None or output_dir is None:
                raise ValueError("input_path, issue_id, and output_dir are required")
            input_value = Path(input_path)
            output_value = Path(output_dir)
            # Register before starting so history has a stable running row.
            # The pipeline binds this row to its timestamped task directory
            # when the worker returns.
            self.current_record = self.task_store.create_task(str(issue_id), input_value)
            request = TaskRequest(
                input_path=input_value,
                issue_id=str(issue_id),
                output_dir=output_value,
                offline=offline,
                no_videos=no_videos,
                resume=resume,
                task_dir=task_dir,
                task_id=task_id or self.current_record.task_id,
            )
        elif request.task_id:
            self.current_record = self.task_store.get_task(request.task_id)
        self.current_request = request
        self.last_error = None
        self.last_result = None
        self.progress_page.reset()
        self.progress_page.set_running(True)
        self.new_task_page.set_busy(True)
        self._token = CancellationToken()
        try:
            runner = self.runner_factory()
        except BaseException as exc:  # startup failures must not strand the UI in Running
            self._on_failed(exc)
            return False
        self._worker = PipelineWorker(runner, request, self._token)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.event.connect(self._on_event)
        self._worker.finished.connect(self._on_finished)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.failed.connect(self._on_failed)
        # ``quit`` is thread-safe.  A direct connection avoids waiting for
        # the GUI event loop when callers close the window and wait for the
        # cooperative worker to finish.
        self._worker.finished.connect(self._thread.quit, Qt.ConnectionType.DirectConnection)
        self._worker.cancelled.connect(self._thread.quit, Qt.ConnectionType.DirectConnection)
        self._worker.failed.connect(self._thread.quit, Qt.ConnectionType.DirectConnection)
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()
        self.task_started.emit(request)
        self.taskStarted.emit(request)
        return True

    def resume_task(self, record: object | None = None) -> bool:
        # Keep the historical checkpoint-resume API while allowing the
        # progress page to use the same verb for an in-place pause/resume.
        if record is None:
            return self.resume_current_task()
        if not isinstance(record, TaskRecord) or self.is_running:
            return False
        if record.input_path is None:
            return False
        input_path = Path(record.input_path)
        output_dir = Path(record.task_root).parent
        options = self.task_store.load_request_options(record)
        request = TaskRequest(
            input_path=input_path,
            issue_id=record.issue_id,
            output_dir=output_dir,
            offline=bool(options.get("offline", False)),
            no_videos=bool(options.get("no_videos", False)),
            config_path=options.get("config_path"),
            history_db=options.get("history_db"),
            max_images=options.get("max_images"),
            llm_provider=options.get("llm_provider"),
            llm_model=options.get("llm_model"),
            resume=True,
            task_dir=Path(record.task_root),
            task_id=record.task_id,
        )
        return self.start_task(request)

    @Slot(object)
    def _on_event(self, event: object) -> None:
        if isinstance(event, ProgressEvent):
            self.progress_page.handle_event(event)
            self.progress_event.emit(event)
            self.progressEvent.emit(event)

    @Slot(object)
    def _on_finished(self, result: object) -> None:
        self.last_result = result
        self._bind_result_task_dir(result)
        self.progress_page.set_finished("Completed")
        self.new_task_page.set_busy(False)
        self.task_finished.emit(result)
        self.taskFinished.emit(result)
        self._quit_thread()

    @Slot(object)
    def _on_cancelled(self, value: object) -> None:
        self.last_result = value
        self._bind_result_task_dir(value)
        self.progress_page.set_finished("Cancelled")
        self.new_task_page.set_busy(False)
        self.task_cancelled.emit(value)
        self.taskCancelled.emit(value)
        self._quit_thread()

    @Slot(object)
    def _on_failed(self, error: object) -> None:
        self.last_error = error if isinstance(error, BaseException) else RuntimeError(str(error))
        self._bind_result_task_dir(error)
        if isinstance(error, BaseException):
            message = str(error) or type(error).__name__
            self.progress_page.report_failure(message)
        else:
            failures = getattr(error, "failures", None) or []
            failure = next(iter(failures), None)
            if failure is not None:
                self.progress_page.report_failure(str(getattr(failure, "message", failure)), count=False)
        self.progress_page.set_finished("Failed")
        self.new_task_page.set_busy(False)
        self.task_failed.emit(error)
        self.taskFailed.emit(error)
        self._quit_thread()

    def _quit_thread(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()

    @Slot()
    def cancel_task(self) -> bool:
        if not self.is_running:
            return False
        self.stop_requested.emit()
        if self._worker is not None:
            self._worker.cancel()
        elif self._token is not None:
            self._token.cancel()
        return True

    stop_task = cancel_task

    @Slot()
    def pause_task(self) -> bool:
        if not self.is_running or self._token is None or self._token.is_cancelled:
            return False
        if self._token.is_paused:
            return True
        if self._worker is not None:
            self._worker.pause()
        else:
            self._token.pause()
        self.progress_page.set_paused(True)
        self.task_paused.emit()
        return True

    @Slot()
    def resume_current_task(self) -> bool:
        if not self.is_running or self._token is None or self._token.is_cancelled:
            return False
        if not self._token.is_paused:
            return True
        if self._worker is not None:
            self._worker.resume()
        else:
            self._token.resume()
        self.progress_page.set_paused(False)
        self.task_resumed.emit()
        return True

    resume_paused_task = resume_current_task

    def wait_for_task(self, timeout_ms: int = 5000) -> bool:
        if self._thread is None:
            return True
        finished = self._thread.wait(max(0, int(timeout_ms)))
        if finished:
            self._on_thread_finished()
        return bool(finished)

    @Slot()
    def _on_thread_finished(self) -> None:
        # A queued signal from a previous run can arrive after resume starts.
        # Qt's sender identity lets that stale signal leave the new run alone
        # without retaining a Python closure around the old QThread.
        sender = self.sender()
        if sender is not None and sender is not self._thread:
            return
        if self._thread is None and self._worker is None and self._token is None:
            return
        self.progress_page.set_running(False)
        self.new_task_page.set_busy(False)
        self._thread = None
        self._worker = None
        self._token = None
        self.history_page.refresh()
        self.history_changed.emit()

    def import_output(self, path: str | Path) -> TaskRecord:
        record = self.task_store.import_output(path)
        self.history_page.refresh()
        self.load_review(record)
        return record

    def load_review(self, record: object) -> ReviewSession | None:
        if not isinstance(record, TaskRecord):
            return None
        self.review_record = record
        source = record.source_output_path
        if source is None:
            raw = record.root_path / "raw"
            source = raw if raw.is_dir() else record.root_path
        try:
            session = ReviewSession.from_output(
                source,
                task_root=record.root_path,
                state_path=record.review_state_path,
            )
        except (OSError, ValueError, TypeError):
            return None
        self.review_page.set_session(session)
        self.review_loaded.emit(session)
        return session

    def _bind_result_task_dir(self, result: object) -> None:
        record = self.current_record
        task_dir = getattr(result, "task_dir", None)
        if record is None or task_dir is None:
            return
        try:
            updated = self.task_store.update_task_root(record.task_id, Path(task_dir))
        except (OSError, TypeError, ValueError):
            return
        if updated is None:
            return
        self.current_record = updated
        if self.current_request is not None:
            request = self.current_request
            self.task_store.save_request_options(
                updated,
                {
                    "offline": request.offline,
                    "no_videos": request.no_videos,
                    "config_path": str(request.config_path) if request.config_path else None,
                    "history_db": str(request.history_db) if request.history_db else None,
                    "max_images": request.max_images,
                    "llm_provider": request.llm_provider,
                    "llm_model": request.llm_model,
                },
            )
        if self.review_record is not None and self.review_record.task_id == updated.task_id:
            self.review_record = updated
        self.history_page.refresh()

    def retry_news(self, news_id: object, official_url: str | None = None) -> bool:
        """Retry one news item into an isolated attempt and merge its data."""

        if isinstance(news_id, dict):
            official_url = str(news_id.get("official_url") or official_url or "")
            news_id = news_id.get("news_id") or news_id.get("id")
        elif isinstance(news_id, (tuple, list)) and len(news_id) >= 1:
            if len(news_id) > 1 and official_url is None:
                official_url = str(news_id[1])
            news_id = news_id[0]
        news_id = str(news_id or "").strip()
        official_url = str(official_url or self.review_page.retry_url()).strip()
        parsed = urlsplit(official_url)
        record = self.review_record or self.current_record
        session = self.review_page.session
        if (
            not news_id
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or record is None
            or session is None
        ):
            return False
        runner = self.runner_factory()
        retry = getattr(runner, "retry_news", None)
        if not callable(retry):
            return False
        try:
            attempt = retry(
                task_root=record.root_path,
                task_id=record.task_id,
                issue_id=record.issue_id,
                news_id=news_id,
                official_url=official_url,
                input_path=record.input_path,
            )
            attempt_id = getattr(attempt, "attempt_id", None)
            if attempt_id is None and isinstance(attempt, dict):
                attempt_id = attempt.get("attempt_id") or attempt.get("id")
            if attempt_id is None:
                path = Path(attempt)
                attempt_id = path.name
            retry_data = self.task_store.merge_retry_attempt(record, news_id, str(attempt_id))
            merged = session.merge_retry_attempt(retry_data)
            self.review_page.refresh()
            self.retry_finished.emit(merged)
            return True
        except (OSError, TypeError, ValueError, KeyError, RuntimeError) as exc:
            self.retry_failed.emit(exc)
            return False

    def apply_theme(self, theme: str) -> str:
        return apply_theme(theme)

    def persist_state(self) -> None:
        # SettingsPage saves the durable settings.  Refreshing history here is
        # intentionally non-destructive and ensures a close/stop sees a fresh
        # catalog in the next process.
        self.history_page.refresh()

    def close(self, timeout_ms: int = 1000) -> bool:
        if self.is_running:
            self.cancel_task()
            if not self.wait_for_task(timeout_ms):
                self.persist_state()
                return False
        self.persist_state()
        return True


__all__ = ["DesktopController"]
