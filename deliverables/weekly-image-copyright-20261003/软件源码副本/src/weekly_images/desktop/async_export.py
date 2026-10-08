"""Run the existing final export away from the GUI thread."""
from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal, Slot


class _Signals(QObject):
    done = Signal(object, object, str)


class _Export(QRunnable):
    def __init__(self, session):
        super().__init__()
        self.session = session
        self.signals = _Signals()

    def run(self):
        try:
            self.signals.done.emit(self.session, self.session.export_final(), "")
        except Exception as exc:
            self.signals.done.emit(self.session, None, f"{type(exc).__name__}: {exc}")


class AsyncExport(QObject):
    finished = Signal(object, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.session = None
        self._job = None

    def start(self, session):
        if self.session is not None:
            return False
        self.session = session
        self._job = _Export(session)
        self._job.signals.done.connect(self._done, Qt.ConnectionType.QueuedConnection)
        self.pool.start(self._job)
        return True

    @Slot(object, object, str)
    def _done(self, session, manifest, error):
        self._job = None
        self.session = None
        self.finished.emit(session, manifest, error)


__all__ = ["AsyncExport"]
