"""Opt-in 3-minute Qt/private-bus soak; fake EC/CPU/GPU only, no installed service.

Run: PYTHONPATH=src QT_QPA_PLATFORM=offscreen python tests/telemetry_soak.py
"""

from collections import Counter
import json
from pathlib import Path
import time

from PyQt6 import QtCore

from test_dbus_integration import PrivateBusTests
from ui.main_window import MainWindow
from ui.theme import apply_theme


def main():
    case = PrivateBusTests()
    case.setUpClass()
    apply_theme(case.app)
    case.setUp()
    window = None
    try:
        fixture = case.start_fixture(stress=True)
        window = MainWindow(case.client)
        window.show()
        updates, heartbeats, history_sizes, workers, memory = [], [], [], [], []
        states = Counter()

        def updated(snapshot):
            updates.append(time.monotonic())
            states[snapshot.reading("gpu_temp_c").status.value] += 1
            history_sizes.append(len(case.client.history))
            status = dict(line.split(":", 1) for line in Path(f"/proc/{fixture.pid}/status").read_text().splitlines())
            workers.append(int(status["Threads"]))
            memory.append(int(status["VmRSS"].split()[0]))
            assert snapshot.cpu_temp_c == 44.0
            assert snapshot.cpu_fan_rpm == 0  # real zero from the fake EC, not a missing sentinel

        case.client.telemetry_updated.connect(updated)
        heartbeat = QtCore.QTimer()
        heartbeat.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        heartbeat.timeout.connect(lambda: heartbeats.append(time.monotonic()))
        heartbeat.start(10)
        QtCore.QTimer.singleShot(180000, case.app.quit)
        case.app.exec()
        intervals = [b - a for a, b in zip(updates, updates[1:])]
        gaps = [b - a for a, b in zip(heartbeats, heartbeats[1:])]
        assert len(updates) >= 175, len(updates)
        assert 0.98 <= sum(intervals) / len(intervals) <= 1.02
        assert max(gaps) < 0.25, max(gaps)
        assert max(history_sizes) == 120
        # Main + three sensor lanes + one executor thread for the identity read.
        assert max(workers) <= 5, max(workers)
        assert states["stale"] > 0 and states["sensor_failed"] > 0
        print(json.dumps({
            "updates": len(updates), "mean_interval_seconds": sum(intervals) / len(intervals),
            "max_qt_heartbeat_gap_seconds": max(gaps), "history_size": len(case.client.history),
            "max_daemon_threads": max(workers), "daemon_rss_first_kib": memory[0],
            "daemon_rss_last_kib": memory[-1], "gpu_states": states,
        }, sort_keys=True))
    finally:
        if window is not None:
            window.close()
        case.doCleanups()


if __name__ == "__main__":
    main()
