"""Bundled Squares / TT Squares fonts: register OTFs and build UI QFonts."""

from __future__ import annotations

import os
import sys

from PyQt6.QtGui import QFont, QFontDatabase

# Default until register_bundled_fonts() discovers the embedded family name
UI_FONT_FAMILY = "TT Squares"


def fonts_directory() -> str:
    try:
        base = sys._MEIPASS  # PyInstaller bundle
    except AttributeError:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "fonts")


def apply_ui_family(name: str) -> None:
    global UI_FONT_FAMILY
    if name:
        UI_FONT_FAMILY = name


def register_bundled_fonts() -> str:
    """Register every OTF/TTF/TTC under fonts/. Return primary family name."""
    d = fonts_directory()
    if not os.path.isdir(d):
        return ""

    exts = (".otf", ".ttf", ".ttc")
    files = [f for f in os.listdir(d) if f.lower().endswith(exts)]

    def sort_key(name: str) -> tuple[int, str]:
        n = name.lower()
        if "regular" in n and "italic" not in n:
            return (0, name)
        if "light" in n and "italic" not in n:
            return (1, name)
        if "bold" in n and "italic" not in n:
            return (2, name)
        return (3, name)

    files.sort(key=sort_key)

    primary = ""
    for fn in files:
        path = os.path.join(d, fn)
        fid = QFontDatabase.addApplicationFont(path)
        if fid != -1:
            fams = QFontDatabase.applicationFontFamilies(fid)
            if fams and not primary:
                primary = fams[0]
    return primary


def font_ui(point_size: int | None = None, *, bold: bool = False) -> QFont:
    f = QFont(UI_FONT_FAMILY)
    f.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
    if point_size is not None:
        f.setPointSize(point_size)
    return f
