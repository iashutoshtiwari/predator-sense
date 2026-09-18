from __future__ import annotations

import os
import sys

from PyQt6 import QtCore, QtGui, QtWidgets

from service.client import ServiceClient
from core.logger import get_logger
from ui.main_window import MainWindow
from ui.theme import APP_ID, APP_NAME, apply_theme, resource_path


def main() -> int:
    if os.geteuid() == 0:
        print("Run predator-sense as your normal desktop user, without sudo or pkexec.", file=sys.stderr)
        return 1
    logger = get_logger(__name__)
    logger.info("PredatorSense starting as the desktop user")

    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName(APP_ID)
    apply_theme(app)
    icon = QtGui.QIcon(resource_path("assets/predator-sense.svg"))
    app.setWindowIcon(icon)
    application = MainWindow(ServiceClient())
    application.setWindowIcon(icon)
    screen = app.primaryScreen()
    if screen is not None:
        # Qt reports logical coordinates. Keep the first window within the usable
        # screen even at 200% scaling; scrollable layouts handle smaller heights.
        available = screen.availableGeometry().size() - QtCore.QSize(16, 48)
        application.resize(application.size().boundedTo(available))
    application.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
