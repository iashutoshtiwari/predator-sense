from PyQt6 import QtCore, QtWidgets

from frontend import Ui_PredatorSense
from service.client import ServiceClient


class MainWindow(QtWidgets.QDialog, Ui_PredatorSense):
    def __init__(self, client: ServiceClient):
        super().__init__()
        self.client = client
        self.last_error = ""
        self.setupUi(self)
        self.manual_timers = {}
        for channel, auto, manual, turbo, slider in self._fan_controls():
            auto.clicked.connect(lambda checked, c=channel: checked and self._set_mode(c, "auto"))
            turbo.clicked.connect(lambda checked, c=channel: checked and self._set_mode(c, "turbo"))
            manual.clicked.connect(lambda checked, c=channel: checked and self._set_mode(c, "manual"))
            timer = QtCore.QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(250)
            timer.timeout.connect(lambda c=channel: self._send_manual(c))
            self.manual_timers[channel] = timer
            slider.valueChanged.connect(lambda _level, c=channel: self.manual_timers[c].start())
        self.global_auto.clicked.connect(lambda checked: checked and self._action(self.client.set_global_auto))
        self.global_turbo.clicked.connect(lambda checked: checked and self._action(self.client.set_global_turbo))
        self.coolboost_checkbox.clicked.connect(
            lambda enabled: self._action(lambda: self.client.set_coolboost(enabled))
        )
        self.exit_button.clicked.connect(self.close)
        self.client.snapshot_changed.connect(self._render)
        self.client.busy_changed.connect(self._busy_changed)
        self.client.failed.connect(self._failed)
        self._render(self.client.snapshot)
        self.refresh_timer = QtCore.QTimer(self)
        self.refresh_timer.setInterval(2000)
        self.refresh_timer.timeout.connect(self._refresh)
        self.refresh_timer.start()
        QtCore.QTimer.singleShot(0, self.client.refresh)

    def _fan_controls(self):
        return (
            ("cpu", self.cpu_auto, self.cpu_manual, self.cpu_turbo, self.verticalSlider),
            ("gpu", self.gpu_auto, self.gpu_manual, self.gpu_turbo, self.verticalSlider_2),
        )

    @staticmethod
    def _select(buttons, selected):
        for button in buttons:
            button.setAutoExclusive(False)
            button.setChecked(button is selected)
        for button in buttons:
            button.setAutoExclusive(True)

    def _refresh(self):
        # Do not reset a slider while the user is choosing its next value.
        if any(timer.isActive() for timer in self.manual_timers.values()):
            return
        self.client.refresh()

    def _render(self, state):
        widgets = [self.global_auto, self.global_turbo, self.coolboost_checkbox]
        widgets.extend(widget for _, *controls in self._fan_controls() for widget in controls)
        blockers = [QtCore.QSignalBlocker(widget) for widget in widgets]
        try:
            ready = state.get("ready", False)
            enabled = ready and not self.client.busy
            for widget in widgets:
                widget.setEnabled(enabled)
            modes = []
            for channel, auto, manual, turbo, slider in self._fan_controls():
                mode = state.get(channel + "_mode", "unknown")
                percent = state.get(channel + "_manual", -1)
                modes.append(mode)
                self._select(
                    (auto, manual, turbo),
                    {
                        "auto": auto,
                        "firmware_auto": auto,
                        "manual": manual,
                        "turbo": turbo,
                    }.get(mode),
                )
                if percent >= 0:
                    slider.setValue((percent + 5) // 10)
                slider.setEnabled(enabled and mode == "manual" and percent >= 0)
            global_selected = None
            if all(mode in ("auto", "firmware_auto") for mode in modes):
                global_selected = self.global_auto
            elif all(mode == "turbo" for mode in modes):
                global_selected = self.global_turbo
            self._select((self.global_auto, self.global_turbo), global_selected)
            boost = state.get("coolboost", -1)
            self.coolboost_checkbox.setTristate(boost < 0)
            if boost < 0:
                self.coolboost_checkbox.setCheckState(QtCore.Qt.CheckState.PartiallyChecked)
            else:
                self.coolboost_checkbox.setChecked(bool(boost))
            text = state.get("message") or "Connected to predator-sensed"
            if ready and self.last_error:
                text = self.last_error
            if self.client.busy:
                text = "Applying change… Complete the authorization dialog if prompted."
            self.status_label.setText(text)
            self.status_label.setToolTip(text)
        finally:
            del blockers

    def _busy_changed(self, _busy):
        self._render(self.client.snapshot)

    def _failed(self, _code, message):
        self.last_error = message
        self._render(self.client.snapshot)

    def _action(self, operation):
        for timer in self.manual_timers.values():
            timer.stop()
        self.last_error = ""
        operation()

    def _set_mode(self, channel, mode):
        self._action(lambda: getattr(self.client, f"set_{channel}_mode")(mode))

    def _send_manual(self, channel):
        slider = self.verticalSlider if channel == "cpu" else self.verticalSlider_2
        percent = slider.value() * 10
        self._action(lambda: getattr(self.client, f"set_{channel}_manual_speed")(percent))
