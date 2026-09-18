"""System-font helpers. Bundled Squares fonts have no confirmed redistribution grant."""

from PyQt6.QtGui import QFont, QFontDatabase


def font_ui(point_size: int | None = None, *, bold: bool = False) -> QFont:
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    if point_size is not None:
        font.setPointSize(point_size)
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return font


def font_numeric(point_size: int, *, bold: bool = False) -> QFont:
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSize(point_size)
    font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
    return font
