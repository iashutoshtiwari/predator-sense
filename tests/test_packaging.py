import ast
from contextlib import redirect_stderr
import io
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from predator_sense.service.protocol import ACTION_ID, BUS_NAME, CONTROL_METHODS, INTERFACE, METHODS

ROOT = Path(__file__).resolve().parent.parent


class PackagingTests(unittest.TestCase):
    def test_bus_policy_has_exact_method_whitelist_and_root_ownership(self):
        root = ElementTree.parse(ROOT / "packaging/io.github.iashutoshtiwari.PredatorSense.conf").getroot()
        default = root.find("policy[@context='default']")
        self.assertIsNotNone(default.find(f"deny[@send_destination='{BUS_NAME}']"))
        allowed = default.findall(f"allow[@send_interface='{INTERFACE}']")
        self.assertEqual({rule.attrib["send_member"] for rule in allowed}, set(METHODS))
        ownership = root.findall(f"policy/allow[@own='{BUS_NAME}']")
        self.assertEqual(len(ownership), 1)
        self.assertIsNotNone(root.find(f"policy[@user='root']/allow[@own='{BUS_NAME}']"))

    def test_polkit_only_active_session_can_authenticate(self):
        root = ElementTree.parse(ROOT / "packaging/io.github.iashutoshtiwari.predatorsense.policy").getroot()
        action = root.find(f"action[@id='{ACTION_ID}']")
        self.assertEqual(action.findtext("defaults/allow_any"), "no")
        self.assertEqual(action.findtext("defaults/allow_inactive"), "no")
        self.assertEqual(action.findtext("defaults/allow_active"), "auth_admin_keep")
        self.assertFalse(action.findall("annotate"))
        self.assertEqual(len(CONTROL_METHODS), 7)

    def test_legacy_paths_are_retired_and_launcher_is_unprivileged(self):
        import tomllib
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        self.assertEqual(project["scripts"]["predator-sense"], "predator_sense.main:main")
        self.assertEqual(project["scripts"]["predator-sensed"], "predator_sense.daemon_main:main")
        self.assertEqual(project["scripts"]["predator-sense-check"], "predator_sense.install_check:main")
        self.assertEqual(project["scripts"]["predator-sense-diagnostics"], "predator_sense.diagnostics:main")
        for path in ("background_service.py", "packaging/predator-sense-root", "packaging/predator-sense.service"):
            self.assertFalse((ROOT / path).exists())
        self.assertIn("disable --now predator-sense.service", (ROOT / "predator-sense.install").read_text())

    def test_gui_has_no_hardware_or_state_writer_imports(self):
        for path in ("src/predator_sense/main.py", "src/predator_sense/frontend.py",
                     "src/predator_sense/ui/main_window.py", "src/predator_sense/service/client.py"):
            tree = ast.parse((ROOT / path).read_text())
            imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
            forbidden = ("predator_sense.core.hardware", "predator_sense.core.env_checks", "predator_sense.core.state")
            self.assertFalse(any(name in forbidden for name in imports))

    def test_root_gui_refused_before_qapplication(self):
        from predator_sense import main

        with patch.object(main.os, "geteuid", return_value=0), patch.object(main.QtWidgets, "QApplication") as app:
            with redirect_stderr(io.StringIO()) as output:
                self.assertEqual(main.main(), 1)
        app.assert_not_called()
        self.assertIn("normal desktop user", output.getvalue())

    def test_wayland_and_x11_startup_do_not_override_qt_platform(self):
        # Session assumptions only: no compositor is started in these tests.
        from predator_sense import main

        for session in ("wayland", "x11"):
            with self.subTest(session=session), patch.dict(os.environ, {"XDG_SESSION_TYPE": session}):
                original = os.environ.get("QT_QPA_PLATFORM")
                with (
                    patch.object(main.os, "geteuid", return_value=1000),
                    patch.object(main.QtWidgets, "QApplication") as app,
                    patch.object(main, "apply_theme"),
                    patch.object(main, "ServiceClient"),
                    patch.object(main, "MainWindow"),
                ):
                    app.return_value.exec.return_value = 0
                    app.return_value.primaryScreen.return_value = None
                    self.assertEqual(main.main(), 0)
                self.assertEqual(os.environ.get("QT_QPA_PLATFORM"), original)

    def test_recipe_installs_all_python_modules_and_service_uses_dbus(self):
        recipe = (ROOT / "PKGBUILD").read_text()
        self.assertIn("python -m build --wheel --no-isolation", recipe)
        self.assertIn("python -m installer", recipe)
        self.assertNotIn("startdir", recipe)
        self.assertNotIn("fonts/", recipe)
        self.assertIn("GPL-3.0-only", recipe)
        unit = (ROOT / "packaging/predator-sensed.service").read_text()
        for setting in (
            "Type=dbus",
            f"BusName={BUS_NAME}",
            "Restart=on-failure",
            "User=root",
            "Conflicts=predator-sense.service",
        ):
            self.assertIn(setting, unit)
