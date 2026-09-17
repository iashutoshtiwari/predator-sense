from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from core.errors import HardwareError
from core.hardware import G3572EcBackend
from core.logger import get_logger
from core.profiles import FanChannel, FanMode
from core.state import STATE_FILE, save_coolboost_state
from frontend import Ui_PredatorSense

logger = get_logger(__name__)


class MainWindow(QtWidgets.QDialog, Ui_PredatorSense):
    def __init__(self, backend: G3572EcBackend):
        super().__init__()
        self.backend = backend
        self.setupUi(self)
        self._refresh()

        for channel, auto, manual, turbo, slider in self._fan_controls():
            auto.clicked.connect(lambda checked, c=channel: checked and self._set_mode(c, FanMode.AUTO))
            turbo.clicked.connect(lambda checked, c=channel: checked and self._set_mode(c, FanMode.TURBO))
            manual.clicked.connect(lambda checked, c=channel: checked and self._set_mode(c, FanMode.MANUAL))
            slider.valueChanged.connect(lambda level, c=channel: self._set_manual(c, level))
        self.global_auto.clicked.connect(lambda checked: checked and self._set_global(FanMode.AUTO))
        self.global_turbo.clicked.connect(lambda checked: checked and self._set_global(FanMode.TURBO))
        self.coolboost_checkbox.clicked.connect(self.toggle_cb)
        self.exit_button.clicked.connect(self.close)

    def _fan_controls(self):
        return (
            (FanChannel.CPU, self.cpu_auto, self.cpu_manual, self.cpu_turbo, self.verticalSlider),
            (FanChannel.GPU, self.gpu_auto, self.gpu_manual, self.gpu_turbo, self.verticalSlider_2),
        )

    @staticmethod
    def _select(buttons, selected):
        # Allow an honest empty selection for UNKNOWN and mixed global states.
        for button in buttons:
            button.setAutoExclusive(False)
            button.setChecked(button is selected)
        for button in buttons:
            button.setAutoExclusive(True)

    def _read(self, operation, fallback):
        try:
            return operation()
        except HardwareError as exc:
            logger.warning("Hardware read failed [%s]: %s", exc.code.value, exc)
            return fallback

    def _refresh(self):
        controls = [self.global_auto, self.global_turbo, self.coolboost_checkbox]
        controls.extend(widget for _, *widgets in self._fan_controls() for widget in widgets)
        blockers = [QtCore.QSignalBlocker(widget) for widget in controls]
        try:
            modes = []
            for channel, auto, manual, turbo, slider in self._fan_controls():
                mode = self._read(lambda: self.backend.get_fan_mode(channel), FanMode.UNKNOWN)
                percent = self._read(lambda: self.backend.get_manual_speed(channel), None)
                modes.append(mode)
                selected = {
                    FanMode.AUTO: auto,
                    FanMode.FIRMWARE_AUTO: auto,
                    FanMode.MANUAL: manual,
                    FanMode.TURBO: turbo,
                }.get(mode)
                self._select((auto, manual, turbo), selected)
                if percent is not None:
                    slider.setValue((percent + 5) // 10)
                slider.setEnabled(mode == FanMode.MANUAL and percent is not None)
                slider.setToolTip("Manual control unavailable" if percent is None else f"Observed control: {percent}%")
                box = self.cpu_box if channel == FanChannel.CPU else self.gpu_box
                box.setToolTip("Fan mode unavailable or unknown" if mode == FanMode.UNKNOWN else mode.value)
            self.cpuFanMode, self.gpuFanMode = modes
            global_selected = None
            if all(mode in (FanMode.AUTO, FanMode.FIRMWARE_AUTO) for mode in modes):
                global_selected = self.global_auto
            elif all(mode == FanMode.TURBO for mode in modes):
                global_selected = self.global_turbo
            self._select((self.global_auto, self.global_turbo), global_selected)
            self.cb = self._read(self.backend.get_coolboost, None)
            self.coolboost_checkbox.setTristate(self.cb is None)
            if self.cb is None:
                self.coolboost_checkbox.setCheckState(QtCore.Qt.CheckState.PartiallyChecked)
            else:
                self.coolboost_checkbox.setChecked(self.cb)
        finally:
            del blockers

    def _apply(self, operation):
        try:
            operation()
        except (HardwareError, OSError) as exc:
            logger.error("Control action failed: %s", exc)
            self._refresh()
            QtWidgets.QMessageBox.warning(self, "Fan control failed", str(exc))
            return
        self._refresh()

    def _set_mode(self, channel: FanChannel, mode: FanMode):
        slider = self.verticalSlider if channel == FanChannel.CPU else self.verticalSlider_2
        percent = slider.value() * 10 if mode == FanMode.MANUAL else None
        self._apply(lambda: self.backend.set_fan_mode(channel, mode, manual_percent=percent))

    def _set_manual(self, channel: FanChannel, level: int):
        self._apply(lambda: self.backend.set_manual_speed(channel, level * 10))

    def _set_global(self, mode: FanMode):
        def apply():
            # Each channel is verified separately; on failure stop and refresh
            # both. EC cannot provide an atomic two-channel hardware commit.
            self.backend.set_fan_mode(FanChannel.CPU, mode)
            self.backend.set_fan_mode(FanChannel.GPU, mode)

        self._apply(apply)

    def toggle_cb(self, enabled: bool):
        def apply():
            self.backend.set_coolboost(enabled)
            save_coolboost_state(enabled, STATE_FILE)

        self._apply(apply)
