from PyQt6 import QtCore, QtWidgets

from predator_sense.frontend import Ui_PredatorSense
from predator_sense.service.client import ServiceClient
from predator_sense.ui.instruments import set_role, set_text
from predator_sense.ui.theme import THEME
from predator_sense.ui.system_info import ModelDiscovery


class MainWindow(QtWidgets.QDialog, Ui_PredatorSense):
    def __init__(self, client: ServiceClient, *, model_discovery=None):
        super().__init__()
        self.client = client
        self.last_error = ""
        self.identity_epoch = None
        self.identity_pending = False
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
            slider.valueChanged.connect(lambda level, c=channel: self._manual_changed(c, level))
            slider.sliderPressed.connect(timer.stop)
            slider.sliderReleased.connect(lambda c=channel: self._send_manual(c))
        self.global_auto.clicked.connect(lambda checked: checked and self._action(self.client.set_global_auto))
        self.global_turbo.clicked.connect(lambda checked: checked and self._action(self.client.set_global_turbo))
        self.coolboost_checkbox.clicked.connect(
            lambda enabled: self._action(lambda: self.client.set_coolboost(enabled))
        )
        self.exit_button.clicked.connect(self.close)
        self.retry_button.clicked.connect(self._retry)
        self.busy_timer = QtCore.QTimer(self)
        self.busy_timer.setSingleShot(True)
        self.busy_timer.setInterval(250)
        self.busy_timer.timeout.connect(self._on_busy_timeout)
        self.busy_prompt = False
        self.client.telemetry_updated.connect(self._telemetry_updated)
        self.client.snapshot_changed.connect(self._render)
        self.client.busy_changed.connect(self._busy_changed)
        self.client.failed.connect(self._failed)
        self._render(self.client.snapshot)
        self.client.start()
        self.model_discovery = model_discovery if model_discovery is not None else ModelDiscovery(self)
        self.model_discovery.names_ready.connect(self._model_names_ready)
        self.model_discovery.start()

    def _model_names_ready(self, cpu, gpu):
        for channel, name in (("cpu", cpu), ("gpu", gpu)):
            label = self.cards[channel].device_name_label
            set_text(label, name)
            label.setToolTip(name)
        self._adapt_layout()

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
                if not self.client.busy:
                    self._select(
                        (auto, manual, turbo),
                        {
                            "auto": auto,
                            "firmware_auto": auto,
                            "manual": manual,
                            "turbo": turbo,
                        }.get(mode),
                    )
                    if percent >= 0 and not self.manual_timers[channel].isActive() and not slider.isSliderDown():
                        slider.setValue((percent + 5) // 10)
                    slider.setEnabled(enabled and mode == "manual" and percent >= 0)
                    if not self.manual_timers[channel].isActive() and not slider.isSliderDown():
                        set_text(self.percent_labels[channel], "—" if percent < 0 else f"{percent}%")
                else:
                    slider.setEnabled(False)
            boost = state.get("coolboost", -1)
            if not self.client.busy:
                global_selected = None
                if all(mode in ("auto", "firmware_auto") for mode in modes):
                    global_selected = self.global_auto
                elif all(mode == "turbo" for mode in modes):
                    global_selected = self.global_turbo
                self._select((self.global_auto, self.global_turbo), global_selected)
                self.coolboost_checkbox.setTristate(boost < 0)
                if boost < 0:
                    self.coolboost_checkbox.setCheckState(QtCore.Qt.CheckState.PartiallyChecked)
                else:
                    self.coolboost_checkbox.setChecked(bool(boost))
            automatic = any(mode in ("auto", "firmware_auto") for mode in modes)
            self.coolboost_checkbox.setEnabled(enabled and automatic and boost >= 0)
            set_text(self.boost_state, "UNKNOWN" if boost < 0 else "ON" if boost else "OFF")
            set_text(self.boost_hint, "CoolBoost state unavailable" if boost < 0 else
                     "Extra cooling for fans in Auto mode" if automatic else "Switch a fan to Auto to use CoolBoost")
            text = state.get("message") or "Connected to predator-sensed"
            if ready and self.last_error:
                text = self.last_error
            busy_prompt = self.client.busy and self.busy_prompt
            if busy_prompt:
                text = "Applying change… Complete the authorization dialog if prompted."
            connected = bool(self.client.telemetry.epoch)
            set_role(self.connection_badge, "badge" if ready and not self.client.busy else "badgeWarn")
            set_text(self.connection_badge, "AUTHORIZATION / APPLYING" if self.client.busy else
                     "CONNECTED" if ready else "CONNECTING" if state.get("code") == "Starting" else
                     "SERVICE ATTENTION" if connected else "OFFLINE")
            set_text(self.daemon_status, "DAEMON CONNECTED" if connected else "DAEMON OFFLINE")
            set_text(self.ec_status, "EC AVAILABLE" if ready else
                     "EC WAITING" if state.get("code") == "Starting" else "EC UNAVAILABLE")
            show_notice = not ready or bool(self.last_error) or busy_prompt or bool(state.get("message"))
            self.notice.setVisible(show_notice)
            self.retry_button.setVisible(not ready and not self.client.busy)
            set_text(self.status_label, text)
            self.status_label.setToolTip(text)
        finally:
            del blockers

    def _telemetry_updated(self, snapshot):
        for card in self.cards.values():
            card.render(snapshot, self.client.history)
        self._adapt_layout()
        if not snapshot.epoch:
            self.identity_epoch = None
            set_text(self.hardware_status, "HARDWARE —")
            set_text(self.bios_status, "BIOS —")
        elif (snapshot.code != "Starting" and snapshot.epoch != self.identity_epoch
              and not self.identity_pending):
            set_text(self.hardware_status, "HARDWARE —")
            set_text(self.bios_status, "BIOS —")
            self.identity_pending = True
            self.identity_epoch = snapshot.epoch
            epoch = snapshot.epoch

            def received(values, error, message):
                self.identity_pending = False
                if self.client.telemetry.epoch != epoch:
                    return
                if error or not values or len(values) != 4:
                    set_text(self.hardware_status, "HARDWARE UNKNOWN")
                    set_text(self.bios_status, "BIOS —")
                    self.hardware_status.setToolTip(message or "Hardware identity unavailable; retry the connection")
                    return
                product, bios, supported, tested = values
                set_text(self.hardware_status, "G3-572 DETECTED" if supported else "UNSUPPORTED HARDWARE")
                self.hardware_status.setToolTip(product)
                set_text(self.bios_status, "BIOS " + (bios or "—"))
                self.bios_status.setToolTip(
                    "Tested BIOS" if tested else "BIOS version has not been physically validated"
                )

            self.client.get_hardware_identity(received)

    def _retry(self):
        self.identity_epoch = None
        self.last_error = ""
        self.client.refresh()

    def _manual_changed(self, channel, level):
        set_text(self.percent_labels[channel], f"{level * 10}%")
        slider = self.verticalSlider if channel == "cpu" else self.verticalSlider_2
        if not slider.isSliderDown():
            self.manual_timers[channel].start()

    def _on_busy_timeout(self):
        if self.client.busy:
            self.busy_prompt = True
            self._render(self.client.snapshot)

    def _busy_changed(self, busy):
        if busy:
            self.busy_prompt = False
            self.busy_timer.start()
        else:
            self.busy_timer.stop()
            self.busy_prompt = False
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
        self.manual_timers[channel].stop()
        if not slider.isEnabled() or self.client.busy:
            return
        percent = slider.value() * 10
        self._action(lambda: getattr(self.client, f"set_{channel}_manual_speed")(percent))

    def _adapt_layout(self):
        needed = sum(card.minimumSizeHint().width() for card in self.cards.values())
        direction = (QtWidgets.QBoxLayout.Direction.TopToBottom
                     if self.width() < needed + 2 * THEME.margin + THEME.spacing + 18
                     else QtWidgets.QBoxLayout.Direction.LeftToRight)
        if self.instrument_layout.direction() != direction:
            self.instrument_layout.setDirection(direction)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "cards"):
            self._adapt_layout()

    def closeEvent(self, event):
        for timer in self.manual_timers.values():
            timer.stop()
        self.client.stop()
        self.model_discovery.stop()
        super().closeEvent(event)
