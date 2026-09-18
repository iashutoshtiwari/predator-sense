"""Typography helpers with Turret Road primary font and system fallback."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QFont, QFontDatabase, QGuiApplication

FONT_FAMILY = "Turret Road"
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
    if QGuiApplication.instance() is not None and FONT_FAMILY in QFontDatabase.families():
        font = QFont(FONT_FAMILY)
        font.setStyleHint(QFont.StyleHint.SansSerif)
    else:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    if point_size is not None:
        font.setPointSize(point_size)
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return font


def font_numeric(point_size: int, *, bold: bool = False) -> QFont:
    _ensure_fonts_loaded()
    if QGuiApplication.instance() is not None and FONT_FAMILY in QFontDatabase.families():
        font = QFont(FONT_FAMILY)
        font.setStyleHint(QFont.StyleHint.SansSerif)
    else:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSize(point_size)
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return font
