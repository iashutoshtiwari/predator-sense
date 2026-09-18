"""Tests for predator_sense.diagnostics module."""

import asyncio
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from predator_sense.diagnostics import (
    collect_diagnostics_report,
    get_os_release,
    main,
    query_daemon_telemetry,
    read_file_safe,
    run_cmd,
)
from predator_sense.service.telemetry_model import TelemetrySnapshot


class DiagnosticsTests(unittest.TestCase):
    def test_read_file_safe_handles_missing_file(self):
        missing = Path("/nonexistent/test/path")
        self.assertIn("unavailable", read_file_safe(missing))

    def test_run_cmd_handles_missing_command(self):
        code, out = run_cmd(["nonexistent-command-xyz-123"])
        self.assertEqual(code, 127)
        self.assertIn("command not found", out)

    def test_get_os_release_reads_distro(self):
        distro = get_os_release()
        self.assertIsInstance(distro, str)
        self.assertTrue(len(distro) > 0)

    def test_collect_diagnostics_excludes_sensitive_identifiers(self):
        report = collect_diagnostics_report()
        joined = "\n".join(report)

        # Hostname and user identity must NOT be leaked
        self.assertNotIn("uid:", joined)
        self.assertNotIn("euid:", joined)
        self.assertNotIn("cwd:", joined)
        self.assertNotIn("Overclock", joined)

        # App version and essential sections must be present
        self.assertIn("app_version: 1.0.0", joined)
        self.assertIn("## System and Hardware", joined)
        self.assertIn("## Service Status", joined)
        self.assertIn("## EC and Kernel Modules", joined)
        self.assertIn("## Graphics Devices", joined)
        self.assertIn("## Daemon Telemetry & Sensors", joined)
        self.assertIn("## EC Register Observations", joined)

    @patch("predator_sense.diagnostics.collect_diagnostics_report")
    def test_main_stdout(self, mock_collect):
        mock_collect.return_value = ["test diagnostic line"]
        with patch("sys.argv", ["predator-sense-diagnostics", "--stdout"]):
            code = main()
            self.assertEqual(code, 0)
