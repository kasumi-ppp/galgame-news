"""Desktop orchestration facade and Qt thread boundary."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any, Callable
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import QMessageBox

from ..pipeline import CancellationToken, PipelineRunner, ProgressEvent, TaskRequest
from ..review import ReviewSession
from ..delivery.asset_paths import resolve_review_index_root
from ..settings import CredentialStore, InMemoryCredentialBackend, SettingsStore
from ..tasks import TaskRecord, TaskStore
from .history_page import HistoryPage
from .new_task_page import NewTaskPage
from .progress_page import ProgressPage
from .review_page import ReviewPage
from .settings_page import SettingsPage
from .theme import apply_theme
from .worker import PipelineWorker, SupplementWorker


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
    supplement_finished = Signal(object)
    supplement_failed = Signal(object)
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
        self.new_task_page.import_requested.connect(self._import_from_page)
        self.progress_page.pause_requested.connect(self.pause_task)
        self.progress_page.resume_requested.connect(self.resume_current_task)
        self.progress_page.stop_requested.connect(self.cancel_task)
        self.history_page.review_requested.connect(self.load_review)
        self.history_page.resume_requested.connect(self.resume_task)
        self.review_page.retry_requested.connect(self._retry_from_page)
        self.retry_finished.connect(lambda *_: self.review_page.show_retry_result(True))
        self.retry_failed.connect(self.review_page.show_retry_failure)
        self.review_page.supplement_requested.connect(self.supplement_news)
        self.review_page.supplement_cancel_requested.connect(self.cancel_supplement)
        self.settings_page.theme_changed.connect(self.apply_theme)

        self._thread: QThread | None = None
        self._worker: PipelineWorker | None = None
        self._token: CancellationToken | None = None
        self._supplement_thread = None
        self._supplement_worker = None
        self._supplement_token = None
        self._supplement_context = None
        self._supplement_active = False
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

    @property
    def is_supplement_running(self) -> bool:
        return self._supplement_active

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
        use_socialdata_x: bool = False,
        selected_sections: list[str] | None = None,
        images_only: bool = False,
        resume: bool = False,
        task_dir: Path | str | None = None,
        task_id: str | None = None,
    ) -> bool:
        if self.is_running or self.is_supplement_running:
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
                use_socialdata_x=use_socialdata_x,
                images_only=images_only,
                selected_sections=(
                    ["x", "h", "z"] if selected_sections is None else selected_sections
                ),
                resume=resume,
                task_dir=task_dir,
                task_id=task_id or self.current_record.task_id,
            )
        elif request.task_id:
            self.current_record = self.task_store.get_task(request.task_id)
        self.current_request = request
        self._save_request_options(self.current_record, request)
        self.last_error = None
        self.last_result = None
        self.progress_page.reset()
        self.progress_page.set_running(True)
        self.new_task_page.set_busy(True)
        self._token = CancellationToken()
        try:
            runner = self.runner_factory()
            self._configure_runner_browser(runner)
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
        if record.input_path is None or record.images_only:
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
            use_socialdata_x=bool(options.get("use_socialdata_x", False)),
            selected_sections=options.get("selected_sections", ["x", "h", "z"]),
            # Older request_options.json files may contain images_only=true.
            # The option is retained for reading compatibility but must never
            # cause an existing task to discard its recovery data on resume.
            images_only=False,
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
            if event.kind == "task_workspace_ready" and self.current_record is not None and self.current_request is not None:
                root = Path(event.payload.get("task_dir", ""))
                if (event.task_id == self.current_record.task_id and root.is_absolute()
                        and root.resolve().is_relative_to(self.current_request.output_dir.resolve())
                        and root.is_dir()):
                    updated = self.task_store.update_task_root(self.current_record, root, materialize=False)
                    if updated is not None:
                        self.current_record = updated
                        if not updated.images_only:
                            self._save_request_options(updated, self.current_request)
            self.progress_page.handle_event(event)
            self.progress_event.emit(event)
            self.progressEvent.emit(event)

    @Slot(object)
    def _on_finished(self, result: object) -> None:
        self.last_result = result
        self._bind_result_task_dir(result)
        self.progress_page.set_finished("Completed")
        self.progress_page.log.appendPlainText(
            "任务目录保留 raw、索引、检查点、缓存、审核状态和 final 图片交付。"
        )
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

    def _import_from_page(self, path: str) -> None:
        try:
            self.import_output(path)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self.new_task_page, "无法导入图片结果", str(exc))

    def load_review(self, record: object) -> ReviewSession | None:
        if self.is_supplement_running:
            return self.review_page.session
        if not isinstance(record, TaskRecord):
            return None
        self.review_record = record
        if record.images_only:
            self.review_page.set_session(None)
            self.review_record = None
            self.progress_page.log.appendPlainText("此任务仅保留图片，请从历史任务打开图片目录人工选择；不支持审核或续跑。")
            return None
        source = record.source_output_path
        if source is None:
            raw = record.root_path / "raw"
            source = raw if raw.is_dir() else record.root_path
        try:
            source = resolve_review_index_root(source)
            # Pipeline indexes live in raw; review export belongs to that
            # same E:/... task, independently of the AppData catalog/state.
            export_root = record.root_path
            if source.name.casefold() == "raw":
                export_root = source.parent
            elif record.imported:
                export_root = source.parent / f"{source.name}_审核结果"
            session = ReviewSession.from_output(
                source,
                task_root=export_root,
                state_path=record.review_state_path,
            )
            session.retry_root = record.root_path / "retries"
            for attempt in self.task_store.list_retry_attempts(record):
                session.merge_retry_attempt(attempt, force_pending=attempt.kind == "supplement")
        except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
            self.review_page.set_session(None)
            self.review_record = None
            QMessageBox.warning(self.review_page, "无法打开图片审核", str(exc))
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
            if getattr(result, "images_only", False):
                updated = self.task_store.update_task_root(record.task_id, Path(task_dir), materialize=False)
            else:
                updated = self.task_store.update_task_root(record.task_id, Path(task_dir))
        except (OSError, TypeError, ValueError):
            return
        if updated is None:
            return
        self.current_record = updated
        if self.current_request is not None and not getattr(result, "images_only", False):
            self._save_request_options(updated, self.current_request)
        if self.review_record is not None and self.review_record.task_id == updated.task_id:
            self.review_record = updated
        self.history_page.refresh()

    def _save_request_options(
        self, record: TaskRecord | None, request: TaskRequest
    ) -> None:
        if record is None:
            return
        self.task_store.save_request_options(
            record,
            {
                "offline": request.offline,
                "no_videos": request.no_videos,
                "use_socialdata_x": request.use_socialdata_x,
                "selected_sections": list(request.selected_sections),
                "images_only": request.images_only,
                "config_path": str(request.config_path) if request.config_path else None,
                "history_db": str(request.history_db) if request.history_db else None,
                "max_images": request.max_images,
                "llm_provider": request.llm_provider,
                "llm_model": request.llm_model,
            },
        )

    def retry_news(self, news_id: object, official_url: str | None = None) -> bool:
        """Retry one news item into an isolated attempt and merge its data."""

        if self.is_running or self.is_supplement_running:
            self.retry_failed.emit(RuntimeError("已有抓取操作正在运行，请稍后重试"))
            return False
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
        self._configure_runner_browser(runner)
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
        except (OSError, TypeError, ValueError, KeyError, RuntimeError, sqlite3.Error) as exc:
            self.retry_failed.emit(exc)
            return False

    @Slot(str, str)
    def supplement_news(self, news_id: str, source_url: str) -> bool:
        """Start one background addition, bound to the current review task."""
        if self.is_running or self.is_supplement_running or self.review_page.exporter.session is not None:
            self.review_page.show_supplement_failure(RuntimeError("已有抓取或导出操作正在运行，请稍后再补抓"))
            if self.is_supplement_running:
                self.review_page.set_supplement_busy(True)
            return False
        record, session = self.review_record, self.review_page.session
        if record is None or session is None:
            return False
        news = next((n for n in session.news_items if str(n.get("news_id")) == str(news_id)), None)
        if news is None:
            self.review_page.show_supplement_failure(ValueError("请先选择一条具体新闻"))
            return False
        try:
            options = self.task_store.load_request_options(record)
            request = TaskRequest(input_path=record.input_path or record.root_path / "input.docx",
                issue_id=record.issue_id, output_dir=record.root_path, task_id=record.task_id,
                no_videos=True, offline=bool(options.get("offline", False)),
                use_socialdata_x=bool(options.get("use_socialdata_x", False)),
                selected_sections=options.get("selected_sections", ["x", "h", "z"]),
                config_path=options.get("config_path"), max_images=options.get("max_images"))
            runner = self.runner_factory()
            self._configure_runner_browser(runner)
            runner._supplement_credential_store = self.credential_store
            self._supplement_token = CancellationToken()
            self._supplement_worker = SupplementWorker(runner, {
                "task_root": record.root_path, "task_id": record.task_id,
                "issue_id": record.issue_id, "news_id": str(news_id),
                "source_url": source_url, "news_item": dict(news), "request": request,
            }, self._supplement_token)
            self._supplement_context = (record, session)
            self._supplement_thread = QThread(self)
            self._supplement_worker.moveToThread(self._supplement_thread)
            self._supplement_thread.started.connect(self._supplement_worker.run)
            self._supplement_worker.event.connect(self._on_supplement_event)
            self._supplement_worker.finished.connect(self._on_supplement_finished)
            self._supplement_worker.failed.connect(self._on_supplement_failed)
            self._supplement_worker.finished.connect(self._supplement_thread.quit, Qt.ConnectionType.DirectConnection)
            self._supplement_worker.failed.connect(self._supplement_thread.quit, Qt.ConnectionType.DirectConnection)
            self._supplement_thread.finished.connect(self._supplement_worker.deleteLater)
            self._supplement_thread.finished.connect(self._on_supplement_thread_finished)
            self._supplement_thread.finished.connect(self._supplement_thread.deleteLater)
            self._supplement_active = True
            self.review_page.set_supplement_busy(True)
            self.new_task_page.set_busy(True)
            self._supplement_thread.start()
            return True
        except Exception as exc:
            self._on_supplement_failed(exc)
            return False

    @Slot(object)
    def _on_supplement_event(self, event):
        self.review_page.operation_status.setText(str(event.message))

    @Slot(object)
    def _on_supplement_finished(self, result):
        try:
            record, session = self._supplement_context
            if result.path.is_dir():
                attempt = self.task_store.merge_retry_attempt(record, result.news_id, result.attempt_id)
                merged = session.merge_retry_attempt(attempt, force_pending=True)
                self.review_page.refresh(preserve_position=True)
                summary = {"added_count": len(merged.added_image_ids), "success_count": result.success_count,
                           "failed_count": result.failed_count, "status": result.status,
                           "failures": [f.model_dump(mode="json") for f in result.failures]}
            else:
                summary = {"added_count": 0, "success_count": 0, "failed_count": 0, "status": result.status}
            self.review_page.show_supplement_result(summary)
            self.supplement_finished.emit(summary)
        except Exception as exc:
            self._on_supplement_failed(exc)

    @Slot(object)
    def _on_supplement_failed(self, error):
        self.review_page.show_supplement_failure(error)
        self.supplement_failed.emit(error)
        if self._supplement_thread is None or not self._supplement_thread.isRunning():
            self._on_supplement_thread_finished()

    @Slot()
    def cancel_supplement(self):
        if self._supplement_token is not None:
            self._supplement_token.cancel()
            self.review_page.operation_status.setText("正在取消补抓，保留已下载图片…")

    @Slot()
    def _on_supplement_thread_finished(self):
        self._supplement_active = False
        self._supplement_thread = self._supplement_worker = self._supplement_token = None
        self._supplement_context = None
        self.review_page.set_supplement_busy(False)
        self.new_task_page.set_busy(False)

    @Slot(str)
    def _retry_from_page(self, news_id: str) -> bool:
        """Give UI retries an outcome even when the public API returns early."""

        failures = []

        def note_failure(error):
            failures.append(error)

        self.retry_failed.connect(note_failure, Qt.ConnectionType.DirectConnection)
        try:
            success = self.retry_news(news_id)
            if not success and not failures:
                self.review_page.show_retry_result(False)
            return success
        except Exception as exc:
            # Runner construction happens before retry_news's exception block.
            # Route that UI failure through the existing diagnostic signal.
            self.retry_failed.emit(exc)
            return False
        finally:
            self.retry_failed.disconnect(note_failure)

    def apply_theme(self, theme: str) -> str:
        return apply_theme(theme)

    def _configure_runner_browser(self, runner) -> None:
        config = getattr(runner, "config", None)
        if config is not None and hasattr(config, "browser"):
            config = config.model_copy(deep=True)
            config.browser.enabled = self.settings_store.load().browser_enabled
            runner.config = config

    def persist_state(self) -> None:
        # SettingsPage saves the durable settings.  Refreshing history here is
        # intentionally non-destructive and ensures a close/stop sees a fresh
        # catalog in the next process.
        self.history_page.refresh()

    def close(self, timeout_ms: int = 1000) -> bool:
        if self.is_supplement_running:
            self.cancel_supplement()
            if self._supplement_thread is not None and not self._supplement_thread.wait(timeout_ms):
                self.persist_state()
                return False
        if self.is_running:
            self.cancel_task()
            if not self.wait_for_task(timeout_ms):
                self.persist_state()
                return False
        self.persist_state()
        return True


__all__ = ["DesktopController"]
