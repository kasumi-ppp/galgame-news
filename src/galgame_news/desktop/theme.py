"""Theme helpers for system/light/dark desktop presentation."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
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


def _dark_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#202124"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#f1f3f4"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#171717"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#2b2b2b"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#f1f3f4"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#303134"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#f1f3f4"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#3f76d3"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    return palette


def apply_theme(theme: str = "system", app: QApplication | None = None) -> str:
    """Apply a small palette override and return the effective theme."""

    app = app or QApplication.instance()
    selected = str(theme or "system").casefold()
    if selected not in {"system", "light", "dark"}:
        selected = "system"
    effective = system_theme(app) if selected == "system" else selected
    if app is not None:
        app.setPalette(_dark_palette() if effective == "dark" else QPalette())
    return effective


__all__ = ["apply_theme", "system_theme"]
