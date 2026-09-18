"""Asynchronous Qt system-bus client. Never imports the hardware implementation."""

from collections import deque
import math
import time

from PyQt6 import QtCore, QtDBus

from predator_sense.service.protocol import BUS_NAME, CONTROL_METHODS, ERROR_PREFIX, INTERFACE, METHODS, OBJECT_PATH
from predator_sense.service.telemetry_model import Availability, HISTORY_LIMIT, INTERVAL, TelemetrySnapshot


class QtBusTransport(QtCore.QObject):
    def __init__(self, parent=None, *, connection=None):
        super().__init__(parent)
        self.connection = connection if connection is not None else QtDBus.QDBusConnection.systemBus()
        self.watchers = set()

    def call(self, member, args, callback):
        message = QtDBus.QDBusMessage.createMethodCall(BUS_NAME, OBJECT_PATH, INTERFACE, member)
        message.setArguments(args)
        timeout = 120000 if member in CONTROL_METHODS else 4000
        watcher = QtDBus.QDBusPendingCallWatcher(self.connection.asyncCall(message, timeout), self)
        self.watchers.add(watcher)

        def finish(_watcher=None):
            if watcher not in self.watchers:
                return
            self.watchers.remove(watcher)
            reply = QtDBus.QDBusPendingReply(watcher)
            if reply.isError():
                error = reply.error()
                callback(None, error.name(), error.message())
            else:
                response = reply.reply()
                if response.signature() != METHODS[member][1]:
                    callback(None, ERROR_PREFIX + "InvalidReply", "Incompatible daemon response; update the package")
                else:
                    callback(response.arguments(), "", "")
            watcher.deleteLater()

        watcher.finished.connect(finish)
        if watcher.isFinished():
            QtCore.QTimer.singleShot(0, finish)


def actionable_error(name, message):
    code = name.removeprefix(ERROR_PREFIX)
    if name.endswith(("ServiceUnknown", "NameHasNoOwner", "Spawn.ExecFailed")):
        return "DaemonUnavailable", "Daemon unavailable. Install/start predator-sensed.service, then retry."
    if name.endswith(("NoReply", "Timeout", "TimedOut", "Disconnected")):
        return (
            "Timeout",
            "Service timed out or disconnected. Check predator-sensed.service; the action may have applied.",
        )
    if code == "AuthorizationCancelled":
        return code, "Authorization cancelled. No new control request was applied."
    if code in ("NotAuthorized", "AuthorizationUnavailable") or name.endswith("AccessDenied"):
        return code, "Authorization denied/unavailable. Check your active session and Polkit authentication agent."
    return code, message


class ServiceClient(QtCore.QObject):
    telemetry_updated = QtCore.pyqtSignal(object)
    snapshot_changed = QtCore.pyqtSignal(object)
    busy_changed = QtCore.pyqtSignal(bool)
    failed = QtCore.pyqtSignal(str, str)

    def __init__(self, parent=None, *, transport=None):
        super().__init__(parent)
        self.transport = transport if transport is not None else QtBusTransport(self)
        self.busy = False
        self.generation = 0
        self.refreshing = False
        self.snapshot = {"ready": False, "code": "Starting", "message": "Connecting to predator-sensed…"}
        self.telemetry = TelemetrySnapshot.empty()
        self.history = deque(maxlen=HISTORY_LIMIT)
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._poll)
        self.running = False
        self.deadline = 0.0

    def start(self):
        if not self.running:
            self.running = True
            self.deadline = time.monotonic()
            self.timer.start(0)

    def stop(self):
        self.running = False
        self.timer.stop()

    def _poll(self):
        if not self.running:
            return
        self.refresh()
        now = time.monotonic()
        self.deadline += max(1, math.floor((now - self.deadline) / INTERVAL) + 1) * INTERVAL
        self.timer.start(max(1, math.ceil((self.deadline - now) * 1000)))

    def _publish_telemetry(self, snapshot):
        self.telemetry = snapshot.aged(time.monotonic())
        if not self.history or (snapshot.epoch, snapshot.sequence) != (
            self.history[-1].epoch, self.history[-1].sequence
        ) or not snapshot.epoch:
            self.history.append(self.telemetry)
        self.telemetry_updated.emit(self.telemetry)
        state = {"ready": self.telemetry.ready, "code": self.telemetry.code, "message": self.telemetry.message}
        for name in ("cpu_mode", "gpu_mode"):
            state[name] = self.telemetry.value(name) or "unknown"
        for name in ("cpu", "gpu"):
            value = self.telemetry.value(name + "_manual_percent")
            state[name + "_manual"] = -1 if value is None else value
        value = self.telemetry.coolboost
        state["coolboost"] = -1 if value is None else int(value)
        self._publish(state)

    def _publish(self, state):
        self.snapshot = state
        self.snapshot_changed.emit(state)

    def refresh(self):
        if self.refreshing:
            # A pending call never creates another call. Age the last sample so a
            # wedged daemon cannot leave fresh-looking controls enabled forever.
            self._publish_telemetry(self.telemetry)
            return
        self.refreshing = True
        generation = self.generation

        def done(values, name, message):
            self.refreshing = False
            if generation != self.generation:
                return
            if not name:
                try:
                    snapshot = TelemetrySnapshot.from_json(values[0])
                except (ValueError, TypeError, IndexError):
                    name, message = ERROR_PREFIX + "InvalidReply", "Invalid telemetry response; update the daemon"
                else:
                    self._publish_telemetry(snapshot)
                    return
            code, text = actionable_error(name, message)
            status = (Availability.SENSOR_FAILED if code == "InvalidReply" else
                      Availability.PERMISSION_DENIED if name.endswith("AccessDenied") else
                      Availability.BACKEND_DISCONNECTED)
            self._publish_telemetry(TelemetrySnapshot.empty(status=status, code=code, message=text))

        self.transport.call("GetTelemetrySnapshot", [], done)

    def _mutate(self, method, *args):
        if self.busy:
            return
        self.generation += 1
        self.busy = True
        self.busy_changed.emit(True)

        def complete(_values, name, message):
            self.busy = False
            self.busy_changed.emit(False)
            if name:
                self.failed.emit(*actionable_error(name, message))
            if not self.running:
                self.refresh()

        self.transport.call(method, list(args), complete)

    def set_cpu_mode(self, mode):
        self._mutate("SetCpuFanMode", mode)

    def set_gpu_mode(self, mode):
        self._mutate("SetGpuFanMode", mode)

    def set_cpu_manual_speed(self, percent):
        self._mutate("SetCpuManualSpeed", percent)

    def set_gpu_manual_speed(self, percent):
        self._mutate("SetGpuManualSpeed", percent)

    def set_coolboost(self, enabled):
        self._mutate("SetCoolBoost", enabled)

    def set_global_auto(self):
        self._mutate("SetGlobalAuto")

    def set_global_turbo(self):
        self._mutate("SetGlobalTurbo")

    def get_hardware_identity(self, callback):
        self.transport.call("GetHardwareIdentity", [], callback)

    def get_temperatures(self, callback):
        self.transport.call("GetTemperatures", [], callback)

    def get_fan_speeds(self, callback):
        self.transport.call("GetFanSpeeds", [], callback)
