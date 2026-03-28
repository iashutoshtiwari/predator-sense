from __future__ import annotations

import os
import sys

from PyQt6 import QtGui, QtWidgets
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette

from core.env_checks import run_env_checks
from core.hardware import ensure_ec_access
from core.logger import get_logger
from core.profiles import G3_572_PROFILE
from font_config import UI_FONT_FAMILY, apply_ui_family, register_bundled_fonts
from ui.main_window import MainWindow


def build_modern_dark_stylesheet() -> str:
    return """
QDialog {
    background-color: #1a1a1a;
    color: #d4d4d4;
}
QTabWidget::pane {
    border: 1px solid #3a3a3a;
    border-radius: 2px;
    background-color: #222222;
    top: -1px;
}
QTabBar::tab {
    background: #1f1f1f;
    color: #9a9a9a;
    border: 1px solid #3a3a3a;
    padding: 6px 12px;
    margin-right: 2px;
    border-top-left-radius: 2px;
    border-top-right-radius: 2px;
}
QTabBar::tab:selected {
    background: #2a2a2a;
    color: #e6e6e6;
    border-color: #4a4a4a;
}
QGroupBox {
    border: 1px solid #3a3a3a;
    border-radius: 2px;
    margin-top: 10px;
    padding-top: 10px;
    background-color: #141414;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    color: #9a9a9a;
}
QRadioButton, QCheckBox {
    color: #c8c8c8;
    spacing: 6px;
}
QRadioButton::indicator, QCheckBox::indicator {
    width: 14px;
    height: 14px;
}
QRadioButton::indicator:unchecked, QCheckBox::indicator:unchecked {
    border: 1px solid #4a4a4a;
    background-color: #1a1a1a;
    border-radius: 2px;
}
QRadioButton::indicator:checked, QCheckBox::indicator:checked {
    border: 1px solid #6a6a6a;
    background-color: #5a5a5a;
    border-radius: 2px;
}
QPushButton {
    background-color: #2a2a2a;
    color: #e6e6e6;
    border: 1px solid #404040;
    border-radius: 2px;
    padding: 6px 12px;
}
QPushButton:hover {
    background-color: #333333;
}
QPushButton:pressed {
    background-color: #1f1f1f;
}
QSlider::groove:vertical {
    background: #2a2a2a;
    width: 4px;
    border-radius: 0;
}
QSlider::handle:vertical {
    background: #707070;
    border: 1px solid #5a5a5a;
    height: 18px;
    margin: -2px -5px;
    border-radius: 2px;
}
QToolTip {
    border: 1px solid #404040;
    background-color: #1a1a1a;
    color: #d4d4d4;
}
"""


def resource_path(relative_path: str) -> str:
    try:
        base_path = sys._MEIPASS
        return os.path.join(base_path, relative_path)
    except Exception:
        pass

    src_base = os.path.dirname(os.path.abspath(__file__))
    src_candidate = os.path.join(src_base, relative_path)
    if os.path.exists(src_candidate):
        return src_candidate

    repo_candidate = os.path.join(os.path.dirname(src_base), relative_path)
    return repo_candidate


def main() -> int:
    logger = get_logger(__name__)
    logger.info("PredatorSense starting")

    if not run_env_checks():
        logger.critical("Environment checks failed; exiting")
        return 1

    if not ensure_ec_access():
        logger.critical("EC access check failed; exiting")
        return 1

    app = QtWidgets.QApplication([])
    primary_family = register_bundled_fonts()
    if primary_family:
        apply_ui_family(primary_family)
    app.setFont(QtGui.QFont(UI_FONT_FAMILY, 10))

    application = MainWindow(G3_572_PROFILE)
    application.setFixedSize(635, 465)
    app.setStyle("Breeze")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Base, QColor(25, 25, 25))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.ToolTipBase, Qt.GlobalColor.black)
    palette.setColor(QPalette.ColorRole.ToolTipText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
    palette.setColor(QPalette.ColorRole.BrightText, Qt.GlobalColor.red)
    palette.setColor(QPalette.ColorRole.Link, QColor(140, 140, 140))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(70, 70, 70))
    palette.setColor(QPalette.ColorRole.HighlightedText, Qt.GlobalColor.black)
    app.setPalette(palette)
    app.setStyleSheet(build_modern_dark_stylesheet())

    application.setWindowIcon(QtGui.QIcon(resource_path("app_icon.ico")))
    application.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
