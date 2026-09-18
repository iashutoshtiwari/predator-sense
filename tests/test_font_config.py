from pathlib import Path
import unittest
from unittest.mock import patch

from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication

import predator_sense.font_config as font_config


class FontConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication([])

    def test_bundled_font_assets_and_license_exist(self):
        font_dir = Path(font_config.__file__).resolve().parent / "assets" / "fonts"
        self.assertTrue(font_dir.is_dir(), "Font asset directory missing")
        for weight_name in ("Regular", "Medium", "Bold"):
            ttf = font_dir / f"TurretRoad-{weight_name}.ttf"
            self.assertTrue(ttf.is_file(), f"{ttf.name} missing")
            self.assertGreater(ttf.stat().st_size, 30_000, f"{ttf.name} unexpectedly small")

        license_file = font_dir / "OFL.txt"
        self.assertTrue(license_file.is_file(), "OFL.txt license missing")
        content = license_file.read_text(encoding="utf-8")
        self.assertIn("SIL OPEN FONT LICENSE Version 1.1", content)
        self.assertIn("The Turret Road Project Authors", content)

    def test_font_ui_and_font_numeric_use_turret_road(self):
        ui_font = font_config.font_ui(12)
        self.assertEqual(ui_font.pointSize(), 12)
        self.assertEqual(ui_font.family(), font_config.FONT_FAMILY)

        ui_bold = font_config.font_ui(10, bold=True)
        self.assertEqual(ui_bold.pointSize(), 10)
        self.assertEqual(ui_bold.family(), font_config.FONT_FAMILY)
        self.assertEqual(ui_bold.weight(), QFont.Weight.DemiBold)

        num_font = font_config.font_numeric(16)
        self.assertEqual(num_font.pointSize(), 16)
        self.assertEqual(num_font.family(), font_config.FONT_FAMILY)

        num_bold = font_config.font_numeric(20, bold=True)
        self.assertEqual(num_bold.pointSize(), 20)
        self.assertEqual(num_bold.family(), font_config.FONT_FAMILY)
        self.assertEqual(num_bold.weight(), QFont.Weight.DemiBold)

    def test_fallback_when_turret_road_unavailable(self):
        with patch.object(font_config, "FONT_FAMILY", "NonExistentFont123"):
            fallback_ui = font_config.font_ui(11)
            expected_general = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
            self.assertEqual(fallback_ui.pointSize(), 11)
            self.assertEqual(fallback_ui.family(), expected_general.family())

            fallback_num = font_config.font_numeric(13)
            expected_fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
            self.assertEqual(fallback_num.pointSize(), 13)
            self.assertEqual(fallback_num.family(), expected_fixed.family())

    def test_safe_execution_when_gui_application_is_none(self):
        with patch("predator_sense.font_config.QGuiApplication.instance", return_value=None):
            font = font_config.font_ui(9)
            self.assertIsInstance(font, QFont)
            self.assertEqual(font.pointSize(), 9)

            num_font = font_config.font_numeric(15)
            self.assertIsInstance(num_font, QFont)
            self.assertEqual(num_font.pointSize(), 15)


if __name__ == "__main__":
    unittest.main()
