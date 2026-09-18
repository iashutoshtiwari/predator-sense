"""Asynchronous Qt system-bus client. Never imports the hardware implementation."""

from PyQt6 import QtCore, QtDBus

from service.protocol import BUS_NAME, CONTROL_METHODS, ERROR_PREFIX, INTERFACE, METHODS, OBJECT_PATH


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

    def _publish(self, state):
        self.snapshot = state
        self.snapshot_changed.emit(state)

    def refresh(self):
        if self.busy or self.refreshing:
            return
        self.refreshing = True
        generation = self.generation

        def stale():
            if generation == self.generation:
                return False
            self.refreshing = False
            if not self.busy:
                self.refresh()
            return True

        def failure(name, message):
            self.refreshing = False
            code, text = actionable_error(name, message)
            self._publish({"ready": False, "code": code, "message": text})

        def status_done(values, name, message):
            if stale():
                return
            if name:
                failure(name, message)
                return
            ready, code, text = values
            if not ready:
                self.refreshing = False
                self._publish({"ready": False, "code": code, "message": text})
                return
            state = {"ready": True, "code": "", "message": text}

            def fans_done(fans, name, message):
                if stale():
                    return
                if name:
                    failure(name, message)
                    return
                state.update(zip(("cpu_mode", "gpu_mode", "cpu_manual", "gpu_manual"), fans))

                def boost_done(boost, name, message):
                    if stale():
                        return
                    if name:
                        failure(name, message)
                        return
                    state["coolboost"] = boost[0]
                    self.refreshing = False
                    self._publish(state)

                self.transport.call("GetCoolBoost", [], boost_done)

            self.transport.call("GetFanState", [], fans_done)

        self.transport.call("GetStatus", [], status_done)

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
