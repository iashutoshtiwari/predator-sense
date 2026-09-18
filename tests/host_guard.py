"""Fail closed at real hardware boundaries; fixtures still inject fake implementations."""

import importlib.abc
import os
import re
import sys

from PyQt6 import QtDBus


class NoRealNvml(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "pynvml":
            raise RuntimeError("Tests must inject NVML instead of importing the real binding")
        return None


def no_system_bus():
    raise RuntimeError("Tests must inject a private Qt D-Bus connection")


def guard(event, args):
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        if os.fsdecode(args[0]).startswith("/sys/kernel/debug/ec/"):
            raise RuntimeError("Tests must not open real EC hardware")
    elif event == "socket.connect" and isinstance(args[1], (str, bytes)):
        if os.fsdecode(args[1]) in ("/run/dbus/system_bus_socket", "/var/run/dbus/system_bus_socket"):
            raise RuntimeError("Tests must use a private D-Bus address")
    elif event == "subprocess.Popen":
        command = str(args[1])
        if re.search(r"\b(lspci|nvidia-smi|modprobe)\b", command):
            raise RuntimeError("Tests must mock hardware discovery/preparation commands")


sys.meta_path.insert(0, NoRealNvml())
sys.addaudithook(guard)
QtDBus.QDBusConnection.systemBus = staticmethod(no_system_bus)
