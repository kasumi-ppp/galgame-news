"""Optional PySide6 desktop toolbox.

The desktop package is deliberately kept behind its own import boundary so
the headless CLI does not import Qt, keyring, or any other desktop-only
dependency.  The public objects below are small, testable seams around the
frozen pipeline, task, review, and settings services.
"""

from .app import create_application, main
from .controller import DesktopController
from .history_page import HistoryPage
from .new_task_page import NewTaskPage
from .progress_page import ProgressPage
from .review_page import ReviewPage
from .settings_page import SettingsPage
from .thumbnail_cache import ThumbnailCache
from .window import MainWindow
from .worker import PipelineWorker

__all__ = [
    "DesktopController",
    "HistoryPage",
    "MainWindow",
    "NewTaskPage",
    "PipelineWorker",
    "ProgressPage",
    "ReviewPage",
    "SettingsPage",
    "ThumbnailCache",
    "create_application",
    "main",
]
