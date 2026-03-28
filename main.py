import os
import sys
import enum
import json
import pathlib
import shutil
import subprocess
from dataclasses import dataclass

from PyQt6 import QtWidgets, QtGui, QtCore
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette, QColor

from frontend import Ui_PredatorSense
from ecwrite import ec_write, ec_read, ensure_ec_access
from font_config import UI_FONT_FAMILY, apply_ui_family, register_bundled_fonts, font_ui

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


class GPUMode(enum.Enum):
    Normal = "normal"
    Faster = "faster"
    Turbo = "turbo"


GPU_MODE_OFFSETS = {
    GPUMode.Normal: (0, 0),
    GPUMode.Faster: (50, 100),
    GPUMode.Turbo: (100, 200),
}


def run_command(cmd: list[str], timeout: int = 8) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"command timed out after {timeout}s"


def locate_coretemp_package_sensor() -> pathlib.Path | None:
    hwmon_dir = pathlib.Path("/sys/class/hwmon")
    if not hwmon_dir.exists():
        return None

    for hw in sorted(hwmon_dir.glob("hwmon*")):
        try:
            name = (hw / "name").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if name != "coretemp":
            continue

        preferred = hw / "temp1_input"
        if preferred.exists():
            return preferred

        for candidate in sorted(hw.glob("temp*_input")):
            if candidate.exists():
                return candidate
    return None


def read_hwmon_temp_c(path: pathlib.Path | None) -> float | None:
    if path is None:
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
        return int(raw) / 1000.0
    except (OSError, ValueError):
        return None


def read_gpu_metrics() -> tuple[float | None, int | None, int | None, int | None]:
    cmd = [
        "nvidia-smi",
        "--query-gpu=temperature.gpu,clocks.gr,clocks.mem,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    code, stdout, _ = run_command(cmd, timeout=6)
    if code != 0:
        return None, None, None, None

    first_line = next((line for line in stdout.splitlines() if line.strip()), "")
    if not first_line:
        return None, None, None, None

    parts = [p.strip() for p in first_line.split(",")]
    if len(parts) < 4:
        return None, None, None, None

    def parse_int(value: str) -> int | None:
        try:
            return int(value)
        except ValueError:
            return None

    temp = parse_int(parts[0])
    core_clock = parse_int(parts[1])
    mem_clock = parse_int(parts[2])
    util = parse_int(parts[3])
    return (float(temp) if temp is not None else None, core_clock, mem_clock, util)


class TemperatureGraphWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.max_points = 120
        self.cpu_history: list[float | None] = []
        self.gpu_history: list[float | None] = []

    def append_sample(self, cpu_temp: float | None, gpu_temp: float | None) -> None:
        self.cpu_history.append(cpu_temp)
        self.gpu_history.append(gpu_temp)
        if len(self.cpu_history) > self.max_points:
            self.cpu_history = self.cpu_history[-self.max_points :]
        if len(self.gpu_history) > self.max_points:
            self.gpu_history = self.gpu_history[-self.max_points :]
        self.update()

    def paintEvent(self, event):
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(10, 10, -10, -10)
        painter.fillRect(rect, QColor("#111111"))
        painter.setPen(QColor("#2f2f2f"))
        painter.drawRect(rect)

        # Temperature grid lines from 30C to 100C.
        min_temp = 30.0
        max_temp = 100.0
        for temp in range(30, 101, 10):
            y = rect.bottom() - ((temp - min_temp) / (max_temp - min_temp)) * rect.height()
            painter.setPen(QColor("#252525"))
            painter.drawLine(rect.left(), int(y), rect.right(), int(y))
            painter.setPen(QColor("#6f6f6f"))
            painter.drawText(rect.left() + 6, int(y) - 2, f"{temp}C")

        self._draw_series(painter, rect, self.cpu_history, QColor("#f04d4d"), min_temp, max_temp)
        self._draw_series(painter, rect, self.gpu_history, QColor("#4db9f0"), min_temp, max_temp)

    def _draw_series(self, painter, rect, series, color, min_temp, max_temp):
        if len(series) < 2:
            return

        step_x = rect.width() / max(1, self.max_points - 1)
        path = QtGui.QPainterPath()
        started = False

        for idx, value in enumerate(series[-self.max_points :]):
            if value is None:
                started = False
                continue

            clipped = max(min_temp, min(max_temp, value))
            x = rect.left() + idx * step_x
            y = rect.bottom() - ((clipped - min_temp) / (max_temp - min_temp)) * rect.height()
            point = QtCore.QPointF(x, y)
            if not started:
                path.moveTo(point)
                started = True
            else:
                path.lineTo(point)

        pen = QtGui.QPen(color, 2)
        painter.setPen(pen)
        painter.drawPath(path)


class NvidiaOverclockController:
    def __init__(self):
        self.available = False
        self.reason = ""
        self.detect()

    def _run_nvidia_settings(self, args: list[str]) -> tuple[int, str, str]:
        return run_command(["nvidia-settings", *args], timeout=10)

    def detect(self) -> None:
        if shutil.which("nvidia-settings") is None:
            self.available = False
            self.reason = "nvidia-settings is not installed"
            return

        if not os.environ.get("DISPLAY"):
            self.available = False
            self.reason = "DISPLAY is not set; launch from desktop session"
            return

        queries = [
            ["-q", "[gpu:0]/GPUGraphicsClockOffsetAllPerformanceLevels"],
            ["-q", "[gpu:0]/GPUMemoryTransferRateOffsetAllPerformanceLevels"],
        ]

        for query in queries:
            code, _out, err = self._run_nvidia_settings(query)
            if code != 0 or "ERROR:" in err:
                self.available = False
                self.reason = (
                    "GPU clock offset control unavailable. "
                    "Enable Coolbits in Xorg/NVIDIA settings if supported."
                )
                return

        self.available = True
        self.reason = ""

    def apply_mode(self, mode: GPUMode) -> tuple[bool, str]:
        if not self.available:
            return False, self.reason or "GPU overclocking is unavailable"

        core_offset, mem_offset = GPU_MODE_OFFSETS[mode]
        args = [
            "-a",
            f"[gpu:0]/GPUGraphicsClockOffsetAllPerformanceLevels={core_offset}",
            "-a",
            f"[gpu:0]/GPUMemoryTransferRateOffsetAllPerformanceLevels={mem_offset}",
        ]
        code, out, err = self._run_nvidia_settings(args)
        if code != 0 or "ERROR:" in err:
            return False, err.strip() or "Failed to apply GPU mode"

        summary = out.strip().splitlines()[:2]
        return True, " | ".join(summary) if summary else "GPU mode applied"


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

        self.cpu_temp_path = locate_coretemp_package_sensor()
        self.oc_controller = NvidiaOverclockController()
        self.setup_monitoring_tab()
        self.start_monitoring()

    def setup_monitoring_tab(self):
        self.monitor_tab = QtWidgets.QWidget()
        monitor_layout = QtWidgets.QVBoxLayout(self.monitor_tab)
        monitor_layout.setContentsMargins(16, 16, 16, 16)
        monitor_layout.setSpacing(10)

        temp_row = QtWidgets.QHBoxLayout()
        self.cpu_temp_label = QtWidgets.QLabel("CPU: -- C")
        self.gpu_temp_label = QtWidgets.QLabel("GPU: -- C")
        self.gpu_metrics_label = QtWidgets.QLabel("GPU clocks: -- / -- MHz | util: --%")
        for label in (self.cpu_temp_label, self.gpu_temp_label, self.gpu_metrics_label):
            label.setFont(font_ui(12, bold=True))
            temp_row.addWidget(label)
        temp_row.addStretch(1)
        monitor_layout.addLayout(temp_row)

        self.temp_graph = TemperatureGraphWidget(self.monitor_tab)
        monitor_layout.addWidget(self.temp_graph)

        legend_row = QtWidgets.QHBoxLayout()
        cpu_legend = QtWidgets.QLabel("CPU line")
        gpu_legend = QtWidgets.QLabel("GPU line")
        cpu_legend.setStyleSheet("color: #f04d4d;")
        gpu_legend.setStyleSheet("color: #4db9f0;")
        legend_row.addWidget(cpu_legend)
        legend_row.addWidget(gpu_legend)
        legend_row.addStretch(1)
        monitor_layout.addLayout(legend_row)

        oc_group = QtWidgets.QGroupBox("GPU Performance Mode")
        oc_layout = QtWidgets.QVBoxLayout(oc_group)
        mode_row = QtWidgets.QHBoxLayout()
        self.gpu_mode_normal = QtWidgets.QRadioButton("Normal")
        self.gpu_mode_faster = QtWidgets.QRadioButton("Faster")
        self.gpu_mode_turbo = QtWidgets.QRadioButton("Turbo")
        self.gpu_mode_normal.setChecked(True)
        for button in (self.gpu_mode_normal, self.gpu_mode_faster, self.gpu_mode_turbo):
            button.setFont(font_ui(12, bold=True))
            mode_row.addWidget(button)
        mode_row.addStretch(1)
        oc_layout.addLayout(mode_row)

        self.gpu_mode_status = QtWidgets.QLabel("Checking NVIDIA overclock support...")
        self.gpu_mode_status.setWordWrap(True)
        oc_layout.addWidget(self.gpu_mode_status)
        monitor_layout.addWidget(oc_group)

        self.fan_control_tab.addTab(self.monitor_tab, "Monitoring")

        self.gpu_mode_normal.toggled.connect(lambda checked: checked and self.apply_gpu_mode(GPUMode.Normal))
        self.gpu_mode_faster.toggled.connect(lambda checked: checked and self.apply_gpu_mode(GPUMode.Faster))
        self.gpu_mode_turbo.toggled.connect(lambda checked: checked and self.apply_gpu_mode(GPUMode.Turbo))

        if self.oc_controller.available:
            self.gpu_mode_status.setText("GPU mode control available. Normal mode selected.")
            ok, msg = self.oc_controller.apply_mode(GPUMode.Normal)
            if not ok:
                self.gpu_mode_status.setText(msg)
                self.set_gpu_mode_controls_enabled(False)
        else:
            self.set_gpu_mode_controls_enabled(False)
            self.gpu_mode_status.setText(self.oc_controller.reason)

    def set_gpu_mode_controls_enabled(self, enabled: bool):
        self.gpu_mode_normal.setEnabled(enabled)
        self.gpu_mode_faster.setEnabled(enabled)
        self.gpu_mode_turbo.setEnabled(enabled)

    def apply_gpu_mode(self, mode: GPUMode):
        ok, message = self.oc_controller.apply_mode(mode)
        if ok:
            self.gpu_mode_status.setText(f"{mode.name} mode applied ({GPU_MODE_OFFSETS[mode][0]} / {GPU_MODE_OFFSETS[mode][1]} MHz offsets).")
        else:
            self.gpu_mode_status.setText(message)

    def start_monitoring(self):
        self.monitor_timer = QtCore.QTimer(self)
        self.monitor_timer.setInterval(2000)
        self.monitor_timer.timeout.connect(self.update_monitoring)
        self.monitor_timer.start()
        self.update_monitoring()

    def update_monitoring(self):
        cpu_temp = read_hwmon_temp_c(self.cpu_temp_path)
        if cpu_temp is None:
            self.cpu_temp_path = locate_coretemp_package_sensor()
            cpu_temp = read_hwmon_temp_c(self.cpu_temp_path)

        gpu_temp, gpu_core_clock, gpu_mem_clock, gpu_util = read_gpu_metrics()

        self.cpu_temp_label.setText(
            f"CPU: {cpu_temp:.1f} C" if cpu_temp is not None else "CPU: unavailable"
        )
        self.gpu_temp_label.setText(
            f"GPU: {gpu_temp:.1f} C" if gpu_temp is not None else "GPU: unavailable"
        )
        if gpu_core_clock is None or gpu_mem_clock is None or gpu_util is None:
            self.gpu_metrics_label.setText("GPU clocks: unavailable")
        else:
            self.gpu_metrics_label.setText(
                f"GPU clocks: {gpu_core_clock} / {gpu_mem_clock} MHz | util: {gpu_util}%"
            )

        self.temp_graph.append_sample(cpu_temp, gpu_temp)

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
