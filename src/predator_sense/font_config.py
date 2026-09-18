"""Typography helpers with Orbitron UI and JetBrains Mono numeric fonts with system fallback."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QFont, QFontDatabase, QGuiApplication

FONT_FAMILY_UI = "Orbitron"
FONT_FAMILY_NUMERIC = "JetBrains Mono"
FONT_FAMILY = FONT_FAMILY_UI
_FONTS_INITIALIZED = False


def _ensure_fonts_loaded() -> None:
    global _FONTS_INITIALIZED
    if _FONTS_INITIALIZED or QGuiApplication.instance() is None:
        return
    _FONTS_INITIALIZED = True
    font_dir = Path(__file__).resolve().parent / "assets" / "fonts"
    if font_dir.is_dir():
        for font_file in sorted(font_dir.glob("*.ttf")):
            QFontDatabase.addApplicationFont(str(font_file))


def font_ui(point_size: int | None = None, *, bold: bool = False) -> QFont:
    _ensure_fonts_loaded()
    if QGuiApplication.instance() is not None and FONT_FAMILY_UI in QFontDatabase.families():
        font = QFont(FONT_FAMILY_UI)
        font.setStyleHint(QFont.StyleHint.SansSerif)
    else:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    if point_size is not None:
        font.setPointSize(point_size)
    font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
    return font


def font_numeric(point_size: int, *, bold: bool = False) -> QFont:
    _ensure_fonts_loaded()
    if QGuiApplication.instance() is not None and FONT_FAMILY_NUMERIC in QFontDatabase.families():
        font = QFont(FONT_FAMILY_NUMERIC)
        font.setStyleHint(QFont.StyleHint.Monospace)
    else:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSize(point_size)
    font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
    return font
