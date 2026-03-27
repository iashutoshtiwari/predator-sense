import os
import sys
import enum
import json
from dataclasses import dataclass

from PyQt6 import QtWidgets, QtGui
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette, QColor

from frontend import Ui_PredatorSense
from ecwrite import ec_write, ec_read, ensure_ec_access
from font_config import UI_FONT_FAMILY, apply_ui_family, register_bundled_fonts

SUPPORTED_MODEL = "Predator G3-572"
STATE_FILE = "/var/lib/predator-sense/state.json"


@dataclass(frozen=True)
class ModelProfile:
    gpu_fan_mode_control: int
    gpu_auto_mode: int
    gpu_turbo_mode: int
    gpu_manual_mode: int
    gpu_manual_speed_control: int
    cool_boost_control: int
    cool_boost_on: int
    cool_boost_off: int
    cpu_fan_mode_control: int
    cpu_auto_mode: int
    cpu_turbo_mode: int
    cpu_manual_mode: int
    cpu_manual_speed_control: int
    cpu_auto_values: tuple[int, ...]
    cpu_turbo_values: tuple[int, ...]
    cpu_manual_values: tuple[int, ...]
    gpu_auto_values: tuple[int, ...]
    gpu_turbo_values: tuple[int, ...]
    gpu_manual_values: tuple[int, ...]


G3_572_PROFILE = ModelProfile(
    gpu_fan_mode_control=0x21,
    gpu_auto_mode=0x50,
    gpu_turbo_mode=0x60,
    gpu_manual_mode=0x70,
    gpu_manual_speed_control=0x3A,
    cool_boost_control=0x10,
    cool_boost_on=0x01,
    cool_boost_off=0x00,
    cpu_fan_mode_control=0x22,
    cpu_auto_mode=0x54,
    cpu_turbo_mode=0x58,
    cpu_manual_mode=0x5C,
    cpu_manual_speed_control=0x37,
    cpu_auto_values=(84, 0),
    cpu_turbo_values=(88,),
    cpu_manual_values=(92, 93),
    gpu_auto_values=(80, 0),
    gpu_turbo_values=(96,),
    gpu_manual_values=(112,),
)


class PFS(enum.Enum):
    Manual = 0
    Auto = 1
    Turbo = 2


class MainWindow(QtWidgets.QDialog, Ui_PredatorSense):
    def __init__(self, profile: ModelProfile):
        super(MainWindow, self).__init__()
        self.profile = profile
        self.setupUi(self)
        self.cb = ec_read(self.profile.cool_boost_control) == 1
        if self.cb:
            self.coolboost_checkbox.setChecked(True)

        t = int(ec_read(self.profile.cpu_fan_mode_control))
        t1 = False

        if t in self.profile.cpu_auto_values:
            self.cpuFanMode = PFS.Auto
            self.cpu_auto.setChecked(True)
        elif t in self.profile.cpu_turbo_values:
            self.cpuFanMode = PFS.Turbo
            self.cpu_turbo.setChecked(True)
            t1 = True
        elif t in self.profile.cpu_manual_values:
            self.cpuFanMode = PFS.Manual
            self.cpu_manual.setChecked(True)
        else:
            print('FOUND', t)
            print('UNKNOWN VALUE FOUND EXIT at cpu box')
            self.cpuauto()

        t = int(ec_read(self.profile.gpu_fan_mode_control))
        t2 = False

        if t in self.profile.gpu_auto_values:
            self.gpuFanMode = PFS.Auto
            self.gpu_auto.setChecked(True)
        elif t in self.profile.gpu_turbo_values:
            self.gpuFanMode = PFS.Turbo
            t2 = True
            self.gpu_turbo.setChecked(True)
        elif t in self.profile.gpu_manual_values:
            self.gpuFanMode = PFS.Manual
            self.gpu_manual.setChecked(True)
        else:
            print('FOUND', t)
            print('UNKNOWN VALUE FOUND EXIT at gpu box')
            self.gpuauto()

        if t1 and t2:
            self.global_turbo.setChecked(True)

        self.cpu_auto.toggled.connect(lambda _: self.cpuauto())
        self.cpu_turbo.toggled.connect(lambda _: self.cpumax())
        self.gpu_auto.toggled.connect(lambda _: self.gpuauto())
        self.gpu_turbo.toggled.connect(lambda _: self.gpumax())
        self.coolboost_checkbox.clicked.connect(self.toggleCB)
        self.verticalSlider.valueChanged.connect(self.cpumanual)
        self.verticalSlider_2.valueChanged.connect(self.gpumanual)
        self.cpu_manual.toggled.connect(lambda _: self.cpusetmanual())
        self.gpu_manual.toggled.connect(lambda _: self.gpusetmanual())
        self.exit_button.clicked.connect(lambda: (print("Exiting..."), sys.exit(0)))

    def cpumax(self):
        ec_write(self.profile.cpu_fan_mode_control, self.profile.cpu_turbo_mode)
        self.cpuFanMode = PFS.Turbo

    def gpumax(self):
        ec_write(self.profile.gpu_fan_mode_control, self.profile.gpu_turbo_mode)
        self.gpuFanMode = PFS.Turbo

    def cpuauto(self):
        ec_write(self.profile.cpu_fan_mode_control, self.profile.cpu_auto_mode)
        self.cpuFanMode = PFS.Auto

    def gpuauto(self):
        ec_write(self.profile.gpu_fan_mode_control, self.profile.gpu_auto_mode)
        self.gpuFanMode = PFS.Auto

    def toggleCB(self, tog):
        print('CoolBoost Toggle: ', end='')
        if tog:
            print('On')
            ec_write(self.profile.cool_boost_control, self.profile.cool_boost_on)
        else:
            print('Off')
            ec_write(self.profile.cool_boost_control, self.profile.cool_boost_off)
        persist_coolboost_state(tog)

    def cpumanual(self, level):
        print(str(level * 10), end=', ')
        print(hex(level * 10))
        ec_write(self.profile.cpu_manual_speed_control, level * 10)

    def gpumanual(self, level):
        print(level * 10, end=', ')
        print(hex(level * 10))
        ec_write(self.profile.gpu_manual_speed_control, level * 10)

    def cpusetmanual(self):
        ec_write(self.profile.cpu_fan_mode_control, self.profile.cpu_manual_mode)
        self.cpuFanMode = PFS.Manual

    def gpusetmanual(self):
        ec_write(self.profile.gpu_fan_mode_control, self.profile.gpu_manual_mode)
        self.gpuFanMode = PFS.Manual


def read_dmi_product_name():
    dmi_path = "/sys/class/dmi/id/product_name"
    try:
        with open(dmi_path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "Unknown"


def ensure_supported_hardware():
    model = read_dmi_product_name()
    if "G3-572" not in model:
        print("Unsupported hardware detected:", model)
        print("This build is scoped to Helios 300 2017 G3-572-55UB.")
        return False
    return True


def persist_coolboost_state(enabled):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"coolboost_enabled": bool(enabled)}, f)
    except OSError as exc:
        print("Warning: failed to persist CoolBoost state:", exc)


def build_modern_dark_stylesheet():
    """Flat minimal dark grey UI (no blue accents)."""
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


if not ensure_supported_hardware():
    sys.exit(1)

if not ensure_ec_access():
    sys.exit(1)

app = QtWidgets.QApplication([])
primary_family = register_bundled_fonts()
if primary_family:
    apply_ui_family(primary_family)
app.setFont(QtGui.QFont(UI_FONT_FAMILY, 10))
application = MainWindow(G3_572_PROFILE)
application.setFixedSize(635, 465) # Makes the window not resizeable
app.setStyle('Breeze')

# Dark theme implementation
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

# Required for the app to have its icon when bundled with PyInstaller
def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base_path, relative_path)


application.setWindowIcon(QtGui.QIcon(resource_path('app_icon.ico')))
application.show()
sys.exit(app.exec())
