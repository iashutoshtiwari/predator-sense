"""Render simulated dashboard states offscreen. Never connects to hardware or D-Bus.

PYTHONPATH=src QT_QPA_PLATFORM=offscreen python tests/render_dashboard.py /tmp/predator-ui
Set QT_SCALE_FACTOR before invoking for fractional/HiDPI renders.
"""

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6 import QtCore, QtWidgets

from dashboard_fixture import DashboardTransport, missing, samples
from predator_sense.service.client import ServiceClient
from predator_sense.ui.instruments import label
from predator_sense.ui.main_window import MainWindow
from predator_sense.ui.theme import apply_theme


def main():
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/predator-ui")
    output.mkdir(parents=True, exist_ok=True)
    app = QtWidgets.QApplication([])
    apply_theme(app)
    for scenario in ("live", "offline", "gpu-unavailable", "authentication", "minimum"):
        history = samples()
        snapshot = history[-1]
        if scenario == "gpu-unavailable":
            snapshot = missing(snapshot, "gpu_temp_c", "gpu_fan_rpm")
            history[-1] = snapshot
        transport = DashboardTransport(snapshot)
        if scenario == "offline":
            history = []
            transport.error = "org.freedesktop.DBus.Error.ServiceUnknown"
        client = ServiceClient(transport=transport)
        client.history.extend(history)
        window = MainWindow(client)
        watermark = label("SIMULATED TEST DATA · OFFSCREEN UI VALIDATION", role="muted", size=8)
        watermark.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        window.layout().addWidget(watermark)
        if scenario == "minimum":
            window.resize(window.minimumSize())
        window.show()
        client.refresh()
        client.stop()
        if scenario == "authentication":
            client.busy = True
            client.busy_changed.emit(True)
        for _ in range(3):
            app.processEvents()
        window.grab().save(str(output / (scenario + ".png")))
        print(scenario, window.size().width(), window.size().height(), "DPR", window.devicePixelRatioF())
        window.close()
        window.deleteLater()
        app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
