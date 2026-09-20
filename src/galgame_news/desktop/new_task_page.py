"""New-task form with DOCX picker and drag/drop support."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class NewTaskPage(QWidget):
    start_requested = Signal(object)
    import_requested = Signal(str)

    def __init__(self, default_output: Path | str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("newTaskPage")
        self.setAcceptDrops(True)
        self.default_output = Path(default_output) if default_output else Path.cwd() / "output"

        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("Drop a .docx file or choose one")
        self.input_edit.setObjectName("inputPathEdit")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.choose_input)
        input_row = QHBoxLayout()
        input_row.addWidget(self.input_edit)
        input_row.addWidget(browse)

        self.issue_edit = QLineEdit()
        self.issue_edit.setPlaceholderText("Issue ID")
        self.issue_edit.setObjectName("issueEdit")
        self.output_edit = QLineEdit(str(self.default_output))
        output_browse = QPushButton("Browse…")
        output_browse.clicked.connect(self.choose_output)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit)
        output_row.addWidget(output_browse)

        self.offline_checkbox = QCheckBox("Offline (use local/indexed sources only)")
        self.no_videos_checkbox = QCheckBox("Skip videos")
        self.start_button = QPushButton("Start task")
        self.start_button.setObjectName("startTaskButton")
        self.start_button.clicked.connect(self.request_start)
        self.import_button = QPushButton("Import legacy output…")
        self.import_button.clicked.connect(self.choose_import)
        self.drop_hint = QLabel("Drop a DOCX here to infer its issue ID")

        form = QFormLayout()
        form.addRow("Input DOCX", input_row)
        form.addRow("Issue", self.issue_edit)
        form.addRow("Output folder", output_row)
        layout = QVBoxLayout(self)
        layout.addWidget(self.drop_hint)
        layout.addLayout(form)
        layout.addWidget(self.offline_checkbox)
        layout.addWidget(self.no_videos_checkbox)
        layout.addWidget(self.start_button)
        layout.addWidget(self.import_button)
        layout.addStretch(1)

    @staticmethod
    def infer_issue(path: Path | str) -> str:
        return Path(path).stem.strip() or "issue"

    def set_input_path(self, path: Path | str) -> None:
        selected = Path(path)
        self.input_edit.setText(str(selected))
        if not self.issue_edit.text().strip():
            self.issue_edit.setText(self.infer_issue(selected))

    def set_busy(self, busy: bool) -> None:
        self.start_button.setEnabled(not busy)
        self.import_button.setEnabled(not busy)

    def request_start(self) -> None:
        payload = {
            "input_path": Path(self.input_edit.text().strip()),
            "issue_id": self.issue_edit.text().strip(),
            "output_dir": Path(self.output_edit.text().strip() or self.default_output),
            "offline": self.offline_checkbox.isChecked(),
            "no_videos": self.no_videos_checkbox.isChecked(),
        }
        if not payload["input_path"].name or not payload["issue_id"]:
            self.drop_hint.setText("Choose a DOCX file and enter an issue ID")
            return
        self.start_requested.emit(payload)

    def request_import(self, path: Path | str) -> None:
        self.import_requested.emit(str(path))

    def choose_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select DOCX", "", "Word document (*.docx)")
        if path:
            self.set_input_path(path)

    def choose_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select output folder", self.output_edit.text())
        if path:
            self.output_edit.setText(path)

    def choose_import(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select legacy output folder", self.output_edit.text())
        if path:
            self.request_import(path)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt API
        if any(url.toLocalFile().casefold().endswith(".docx") for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt API
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.casefold().endswith(".docx"):
                self.set_input_path(path)
                event.acceptProposedAction()
                return
        event.ignore()


__all__ = ["NewTaskPage"]
