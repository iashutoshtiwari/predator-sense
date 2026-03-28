from __future__ import annotations

import json
import os
import sys

from PyQt6 import QtWidgets

from core.hardware import (
    ec_read,
    ec_write,
)
from core.logger import get_logger
from core.profiles import ModelProfile, PFS
from frontend import Ui_PredatorSense


logger = get_logger(__name__)
STATE_FILE = "/var/lib/predator-sense/state.json"


class MainWindow(QtWidgets.QDialog, Ui_PredatorSense):
    def __init__(self, profile: ModelProfile):
        super().__init__()
        self.profile = profile
        self.setupUi(self)

        self.cb = ec_read(self.profile.cool_boost_control) == 1
        if self.cb:
            self.coolboost_checkbox.setChecked(True)

        cpu_raw = ec_read(self.profile.cpu_fan_mode_control)
        gpu_raw = ec_read(self.profile.gpu_fan_mode_control)

        t1 = False
        t2 = False

        if cpu_raw in self.profile.cpu_auto_values:
            self.cpuFanMode = PFS.Auto
            self.cpu_auto.setChecked(True)
        elif cpu_raw in self.profile.cpu_turbo_values:
            self.cpuFanMode = PFS.Turbo
            self.cpu_turbo.setChecked(True)
            t1 = True
        elif cpu_raw in self.profile.cpu_manual_values:
            self.cpuFanMode = PFS.Manual
            self.cpu_manual.setChecked(True)
        else:
            logger.warning("Unknown CPU fan mode value %s. Falling back to auto mode.", cpu_raw)
            self.cpuauto()

        if gpu_raw in self.profile.gpu_auto_values:
            self.gpuFanMode = PFS.Auto
            self.gpu_auto.setChecked(True)
        elif gpu_raw in self.profile.gpu_turbo_values:
            self.gpuFanMode = PFS.Turbo
            self.gpu_turbo.setChecked(True)
            t2 = True
        elif gpu_raw in self.profile.gpu_manual_values:
            self.gpuFanMode = PFS.Manual
            self.gpu_manual.setChecked(True)
        else:
            logger.warning("Unknown GPU fan mode value %s. Falling back to auto mode.", gpu_raw)
            self.gpuauto()

        if t1 and t2:
            self.global_turbo.setChecked(True)

        self.cpu_auto.toggled.connect(lambda _: self.cpuauto())
        self.cpu_turbo.toggled.connect(lambda _: self.cpumax())
        self.gpu_auto.toggled.connect(lambda _: self.gpuauto())
        self.gpu_turbo.toggled.connect(lambda _: self.gpumax())
        self.coolboost_checkbox.clicked.connect(self.toggle_cb)
        self.verticalSlider.valueChanged.connect(self.cpumanual)
        self.verticalSlider_2.valueChanged.connect(self.gpumanual)
        self.cpu_manual.toggled.connect(lambda _: self.cpusetmanual())
        self.gpu_manual.toggled.connect(lambda _: self.gpusetmanual())
        self.exit_button.clicked.connect(self._exit_app)

    def _exit_app(self):
        logger.info("Exiting PredatorSense")
        sys.exit(0)

    def cpumax(self):
        if ec_write(self.profile.cpu_fan_mode_control, self.profile.cpu_turbo_mode):
            self.cpuFanMode = PFS.Turbo

    def gpumax(self):
        if ec_write(self.profile.gpu_fan_mode_control, self.profile.gpu_turbo_mode):
            self.gpuFanMode = PFS.Turbo

    def cpuauto(self):
        if ec_write(self.profile.cpu_fan_mode_control, self.profile.cpu_auto_mode):
            self.cpuFanMode = PFS.Auto

    def gpuauto(self):
        if ec_write(self.profile.gpu_fan_mode_control, self.profile.gpu_auto_mode):
            self.gpuFanMode = PFS.Auto

    def toggle_cb(self, toggled: bool):
        if toggled:
            logger.info("CoolBoost toggled on")
            ec_write(self.profile.cool_boost_control, self.profile.cool_boost_on)
        else:
            logger.info("CoolBoost toggled off")
            ec_write(self.profile.cool_boost_control, self.profile.cool_boost_off)
        persist_coolboost_state(toggled)

    def cpumanual(self, level: int):
        value = level * 10
        logger.debug("CPU manual fan level set to %d", value)
        ec_write(self.profile.cpu_manual_speed_control, value)

    def gpumanual(self, level: int):
        value = level * 10
        logger.debug("GPU manual fan level set to %d", value)
        ec_write(self.profile.gpu_manual_speed_control, value)

    def cpusetmanual(self):
        if ec_write(self.profile.cpu_fan_mode_control, self.profile.cpu_manual_mode):
            self.cpuFanMode = PFS.Manual

    def gpusetmanual(self):
        if ec_write(self.profile.gpu_fan_mode_control, self.profile.gpu_manual_mode):
            self.gpuFanMode = PFS.Manual


def persist_coolboost_state(enabled: bool):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as file_handle:
            json.dump({"coolboost_enabled": bool(enabled)}, file_handle)
    except OSError as exc:
        logger.warning("Failed to persist CoolBoost state: %s", exc)
