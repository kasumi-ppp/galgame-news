"""Theme helpers for system/light/dark desktop presentation."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette, QFont, QFontDatabase
from PySide6.QtWidgets import QApplication


def system_theme(app: QApplication | None = None) -> str:
    """Return the current platform color scheme as ``light`` or ``dark``."""

    app = app or QApplication.instance()
    if app is not None:
        try:
            scheme = app.styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return "dark"
            if scheme == Qt.ColorScheme.Light:
                return "light"
        except AttributeError:
            pass
        try:
            window = app.palette().color(QPalette.ColorRole.Window)
            return "dark" if window.lightness() < 128 else "light"
        except (AttributeError, RuntimeError):
            pass
    return "light"


def _palette(dark: bool) -> QPalette:
    palette = QPalette()
    colors = {
        "Window": "#242231" if dark else "#f5f3fb",
        "WindowText": "#f0edf8" if dark else "#302c46",
        "Base": "#302d40" if dark else "#ffffff",
        "AlternateBase": "#383448" if dark else "#efecf9",
        "Text": "#f0edf8" if dark else "#302c46",
        "Button": "#383448" if dark else "#ffffff",
        "ButtonText": "#f0edf8" if dark else "#302c46",
        "Highlight": "#7860b5", "HighlightedText": "#ffffff",
        "Mid": "#504a65" if dark else "#ded9ee",
        "PlaceholderText": "#aaa2bd" if dark else "#827a98",
        "ToolTipBase": "#302d40" if dark else "#ffffff",
        "ToolTipText": "#f0edf8" if dark else "#302c46",
    }
    for role, color in colors.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#8c8799"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#8c8799"))
    return palette


STYLE = """
QWidget { color: palette(window-text); }
QMainWindow, QStackedWidget { background: palette(window); }
QLabel { background: transparent; }
QLabel#pageTitle { font-size: 25px; font-weight: 700; }
QLabel#brand { font-size: 19px; font-weight: 700; }
QLabel#muted { color: palette(placeholder-text); }
QFrame#sidebar, QGroupBox, QFrame#card { background: palette(base); border: 1px solid palette(mid); border-radius: 14px; }
QGroupBox { margin-top: 16px; padding: 22px 16px 14px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 18px; padding: 2px 8px; }
QLineEdit, QComboBox, QPlainTextEdit { background: palette(base); border: 1px solid palette(mid); border-radius: 8px; padding: 8px; selection-background-color: palette(highlight); }
QLineEdit:focus, QComboBox:focus { border: 1px solid #9680d5; }
QPushButton { background: palette(button); border: 1px solid palette(mid); border-radius: 8px; padding: 9px 15px; }
QPushButton:hover { border-color: #9680d5; background: palette(alternate-base); }
QPushButton:pressed { background: palette(mid); }
QPushButton[primary="true"] { background: #7860b5; color: white; border: 1px solid #7860b5; font-weight: 600; }
QPushButton[primary="true"]:hover { background: #8b72c5; }
QPushButton:disabled { color: #938caa; background: palette(alternate-base); }
QCheckBox { spacing: 9px; padding: 7px 0; }
QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid palette(mid); border-radius: 4px; background: palette(base); }
QCheckBox::indicator:checked { background: #9680d5; border: 3px solid #cbbbed; }
QListWidget, QTreeWidget { background: palette(base); border: 1px solid palette(mid); border-radius: 12px; padding: 6px; outline: none; }
QListWidget::item, QTreeWidget::item { padding: 10px; border-radius: 8px; }
QListWidget::item:selected, QTreeWidget::item:selected { background: palette(alternate-base); color: palette(text); border: 1px solid #b5a4df; }
QListWidget#navigation { background: transparent; border: none; padding: 0; }
QListWidget#navigation::item { padding: 15px 10px; margin: 4px 0; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: palette(base); padding: 10px 15px; border: 1px solid palette(mid); }
QTabBar::tab:selected { color: #9680d5; border-bottom: 3px solid #9680d5; }
QProgressBar { background: palette(base); border: 1px solid palette(mid); border-radius: 7px; text-align: center; min-height: 23px; }
QProgressBar::chunk { background: #a58cd7; border-radius: 6px; }
QSplitter::handle { background: palette(window); width: 7px; height: 7px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: palette(mid); border-radius: 5px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { padding: 7px; border: 1px solid palette(mid); }
"""


def _prepare_font(app: QApplication) -> None:
    if app.property("toolboxFontReady"):
        return
    from pathlib import Path
    # Qt offscreen environments may not discover installed Windows fonts.
    for filename in ("msyh.ttc", "msyhbd.ttc"):
        path = Path("C:/Windows/Fonts") / filename
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
    font = QFont("Microsoft YaHei UI", 10)
    font.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans CJK SC", "sans-serif"])
    app.setFont(font)
    app.setProperty("toolboxFontReady", True)


def apply_theme(theme: str = "system", app: QApplication | None = None) -> str:
    """Apply a small palette override and return the effective theme."""

    app = app or QApplication.instance()
    selected = str(theme or "system").casefold()
    if selected not in {"system", "light", "dark"}:
        selected = "system"
    effective = system_theme(app) if selected == "system" else selected
    if app is not None:
        if not app.property("toolboxThemeConnected"):
            app.setProperty("toolboxSystemDark", system_theme(app) == "dark")
            app.styleHints().colorSchemeChanged.connect(
                lambda scheme: _system_changed(app, scheme)
            )
            app.setProperty("toolboxThemeConnected", True)
        if selected == "system":
            effective = "dark" if app.property("toolboxSystemDark") else "light"
        app.setProperty("toolboxTheme", selected)
        if app.style().objectName().casefold() != "fusion":
            app.setStyle("Fusion")
        _prepare_font(app)
        app.setPalette(_palette(effective == "dark"))
        app.setStyleSheet(STYLE)
    return effective


def _system_changed(app: QApplication, scheme) -> None:
    app.setProperty("toolboxSystemDark", scheme == Qt.ColorScheme.Dark)
    if app.property("toolboxTheme") == "system":
        apply_theme("system", app)


__all__ = ["apply_theme", "system_theme"]
