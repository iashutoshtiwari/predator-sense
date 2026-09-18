"""Shared desktop identity, colors, spacing, and Qt stylesheet."""

from dataclasses import dataclass
from pathlib import Path

from PyQt6 import QtGui

from predator_sense.font_config import font_ui

APP_ID = "io.github.iashutoshtiwari.PredatorSense"
APP_NAME = "Predator Sense"


@dataclass(frozen=True)
class Theme:
    background: str = "#0B0D0F"
    surface: str = "#11151A"
    raised: str = "#171C22"
    border: str = "#252C34"
    text: str = "#E8EBEF"
    muted: str = "#98A2AD"
    accent: str = "#ED334B"
    accent_hover: str = "#FF5066"
    accent_dark: str = "#391820"
    warning: str = "#EDB96A"
    success: str = "#85BEA4"
    disabled: str = "#727E8B"
    spacing: int = 12
    margin: int = 24


THEME = Theme()


def resource_path(relative):
    return str(Path(__file__).resolve().parents[1] / relative)


def stylesheet():
    t = THEME
    return f"""
    QDialog, QScrollArea, QWidget#dashboard {{ background: {t.background}; color: {t.text}; }}
    QLabel {{ color: {t.text}; background: transparent; }}
    QLabel[role="muted"], QLabel[role="eyebrow"] {{ color: {t.muted}; }}
    QLabel[role="accent"] {{ color: {t.accent}; }}
    QLabel[role="badge"] {{ color: {t.success}; padding: 6px 10px; border: 1px solid {t.border}; }}
    QLabel[role="badgeWarn"] {{ color: {t.warning}; padding: 6px 10px; border: 1px solid {t.border}; }}
    QFrame[panel="true"] {{ background: {t.surface}; border: 1px solid {t.border}; border-radius: 3px; }}
    QFrame#notice {{ background: {t.raised}; border-left: 3px solid {t.warning}; }}
    QFrame#line {{ background: {t.border}; border: none; }}
    QRadioButton {{ background: {t.raised}; border: 1px solid {t.border}; padding: 9px 13px;
                     color: {t.muted}; spacing: 0; }}
    QRadioButton::indicator {{ width: 0; height: 0; }}
    QRadioButton:hover {{ color: {t.text}; border-color: {t.muted}; }}
    QRadioButton:checked {{ background: {t.accent_dark}; color: {t.text}; border-color: {t.accent}; }}
    QRadioButton:focus, QPushButton:focus, QCheckBox:focus {{ outline: 2px solid {t.accent_hover}; }}
    QRadioButton:disabled {{ color: {t.disabled}; border-color: {t.border}; background: {t.surface}; }}
    QRadioButton:checked:disabled {{ border-color: {t.muted}; background: {t.raised}; }}
    QPushButton {{ background: {t.raised}; border: 1px solid {t.border}; color: {t.text}; padding: 8px 14px; }}
    QPushButton:hover {{ border-color: {t.muted}; }}
    QPushButton:pressed {{ background: {t.accent_dark}; }}
    QSlider::groove:horizontal {{ height: 4px; background: {t.border}; }}
    QSlider::sub-page:horizontal {{ background: {t.accent}; }}
    QSlider::handle:horizontal {{ width: 12px; margin: -6px 0; background: {t.text}; border: 1px solid {t.text}; }}
    QSlider::handle:horizontal:hover, QSlider::handle:horizontal:focus {{ background: {t.accent_hover}; }}
    QSlider:focus {{ border: 1px solid {t.accent_hover}; }}
    QSlider::sub-page:horizontal:disabled {{ background: {t.border}; }}
    QSlider::handle:horizontal:disabled {{ background: {t.disabled}; border-color: {t.disabled}; }}
    QCheckBox {{ color: {t.text}; spacing: 12px; padding: 5px; }}
    QCheckBox::indicator {{ width: 32px; height: 16px; border: 1px solid {t.muted}; border-radius: 8px;
                           background: {t.border}; }}
    QCheckBox::indicator:checked {{ background: {t.accent}; border-color: {t.accent}; }}
    QCheckBox::indicator:indeterminate {{ background: {t.raised}; border: 1px dashed {t.muted}; }}
    QCheckBox::indicator:hover {{ border-color: {t.accent_hover}; }}
    QCheckBox:disabled {{ color: {t.disabled}; }}
    QCheckBox::indicator:disabled {{ background: {t.border}; border-color: {t.disabled}; }}
    QToolTip {{ color: {t.text}; background: {t.raised}; border: 1px solid {t.border}; padding: 5px; }}
    QScrollArea {{ border: none; }}
    """


def apply_theme(app):
    app.setFont(font_ui(10))
    palette = QtGui.QPalette()
    for role, color in (("Window", THEME.background), ("WindowText", THEME.text),
                        ("Base", THEME.surface), ("Text", THEME.text), ("Button", THEME.raised),
                        ("ButtonText", THEME.text), ("Highlight", THEME.accent),
                        ("HighlightedText", THEME.text)):
        palette.setColor(getattr(QtGui.QPalette.ColorRole, role), QtGui.QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet())
