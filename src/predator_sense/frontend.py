"""Layout-managed native dashboard. Presentation only; actions live in MainWindow."""

from PyQt6 import QtCore, QtGui, QtWidgets

from predator_sense.font_config import font_numeric, font_ui
from predator_sense.ui.instruments import CoolBoostSwitch, TelemetryCard, label
from predator_sense.ui.theme import APP_NAME, THEME, resource_path


def segments(names, accessible_prefix):
    container = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    buttons = []
    for name in names:
        button = QtWidgets.QRadioButton(name, container)
        button.setFont(font_ui(9, bold=True))
        button.setAccessibleName(f"{accessible_prefix} {name.lower()}")
        button.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        layout.addWidget(button)
        buttons.append(button)
    return container, buttons


class BrandMark(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.icon = QtGui.QIcon(resource_path("assets/predator-sense.svg"))
        self.setFixedSize(42, 42)
        self.setAccessibleName("Predator Sense")

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        self.icon.paint(painter, self.rect())


class Ui_PredatorSense:
    def setupUi(self, window):
        window.setObjectName("PredatorSense")
        window.setWindowTitle(APP_NAME)
        window.resize(1020, 800)
        window.setMinimumSize(760, 480)
        outer = QtWidgets.QVBoxLayout(window)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.dashboard = QtWidgets.QWidget()
        self.dashboard.setObjectName("dashboard")
        scroll.setWidget(self.dashboard)
        outer.addWidget(scroll)
        root = QtWidgets.QVBoxLayout(self.dashboard)
        root.setContentsMargins(THEME.margin, 20, THEME.margin, 16)
        root.setSpacing(THEME.spacing)

        header = QtWidgets.QHBoxLayout()
        header.addWidget(BrandMark())
        title = QtWidgets.QVBoxLayout()
        title.setSpacing(2)
        title.addWidget(label("PREDATOR SENSE", size=19, bold=True))
        title.addWidget(label("THERMAL CONTROL  /  G3-572", role="muted", size=8))
        header.addLayout(title)
        header.addStretch()
        self.connection_badge = label("CONNECTING", role="badge", size=9, bold=True)
        header.addWidget(self.connection_badge)
        root.addLayout(header)

        self.notice = QtWidgets.QFrame()
        self.notice.setObjectName("notice")
        notice_layout = QtWidgets.QHBoxLayout(self.notice)
        notice_layout.setContentsMargins(12, 8, 12, 8)
        self.status_label = label("Connecting to hardware service…", size=9)
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("Hardware service status")
        notice_layout.addWidget(self.status_label, 1)
        self.retry_button = QtWidgets.QPushButton("Retry")
        self.retry_button.setAutoDefault(False)
        self.retry_button.setAccessibleName("Retry daemon connection")
        notice_layout.addWidget(self.retry_button)
        root.addWidget(self.notice)

        instruments = QtWidgets.QHBoxLayout()
        self.instrument_layout = instruments
        instruments.setSpacing(THEME.spacing)
        self.cards = {channel: TelemetryCard(channel) for channel in ("cpu", "gpu")}
        for card in self.cards.values():
            instruments.addWidget(card, 1)
        root.addLayout(instruments, 1)

        self.cooling_panel = QtWidgets.QFrame()
        self.cooling_panel.setProperty("panel", True)
        cooling = QtWidgets.QVBoxLayout(self.cooling_panel)
        cooling.setContentsMargins(20, 16, 20, 16)
        cooling.setSpacing(14)
        heading = QtWidgets.QHBoxLayout()
        heading.addWidget(label("COOLING CONTROL", size=11, bold=True))
        heading.addStretch()
        heading.addWidget(label("BOTH FANS", role="muted", size=8))
        global_group, (self.global_auto, self.global_turbo) = segments(("AUTO", "TURBO"), "Both fans")
        heading.addWidget(global_group)
        cooling.addLayout(heading)
        self.percent_labels = {}
        for channel in ("cpu", "gpu"):
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(14)
            row.addWidget(label(channel.upper(), size=10, bold=True))
            group, buttons = segments(("AUTO", "MANUAL", "TURBO"), channel.upper() + " fan")
            for name, button in zip(("auto", "manual", "turbo"), buttons):
                setattr(self, channel + "_" + name, button)
            row.addWidget(group)
            slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
            slider.setRange(0, 10)
            slider.setPageStep(1)
            slider.setMinimumWidth(110)
            slider.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
            slider.setAccessibleName(channel.upper() + " manual fan speed")
            slider.setToolTip("Manual fan setting · 10% steps · applied on release or after a short keyboard pause")
            # Keep caller-facing names compatible with the earlier controller.
            setattr(self, "verticalSlider" if channel == "cpu" else "verticalSlider_2", slider)
            row.addWidget(slider, 1)
            percent = label("—")
            percent.setFont(font_numeric(11, bold=True))
            percent.setMinimumWidth(percent.fontMetrics().horizontalAdvance("100%") + 4)
            percent.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
            self.percent_labels[channel] = percent
            row.addWidget(percent)
            cooling.addLayout(row)
        line = QtWidgets.QFrame()
        line.setObjectName("line")
        line.setFixedHeight(1)
        cooling.addWidget(line)
        boost_row = QtWidgets.QHBoxLayout()
        self.coolboost_checkbox = CoolBoostSwitch("CoolBoost")
        self.coolboost_checkbox.setFont(font_ui(11, bold=True))
        self.coolboost_checkbox.setAccessibleName("CoolBoost for automatic fan cooling")
        self.coolboost_checkbox.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        boost_row.addWidget(self.coolboost_checkbox)
        self.boost_hint = label("Extra cooling in Auto mode", role="muted", size=9)
        self.boost_hint.setWordWrap(True)
        boost_row.addWidget(self.boost_hint, 1)
        self.boost_state = label("UNKNOWN", role="muted", size=9, bold=True)
        boost_row.addWidget(self.boost_state)
        cooling.addLayout(boost_row)
        root.addWidget(self.cooling_panel)

        footer = QtWidgets.QHBoxLayout()
        footer.setSpacing(16)
        self.daemon_status = label("DAEMON —", role="muted", size=8)
        self.hardware_status = label("HARDWARE —", role="muted", size=8)
        self.ec_status = label("EC —", role="muted", size=8)
        self.bios_status = label("BIOS —", role="muted", size=8)
        for widget in (self.daemon_status, self.hardware_status, self.ec_status, self.bios_status):
            footer.addWidget(widget)
        footer.addStretch()
        self.exit_button = QtWidgets.QPushButton("Close")
        self.exit_button.setAutoDefault(False)
        footer.addWidget(self.exit_button)
        root.addLayout(footer)
        order = [self.retry_button, self.global_auto, self.global_turbo, self.cpu_auto, self.cpu_manual,
                 self.cpu_turbo, self.verticalSlider, self.gpu_auto, self.gpu_manual, self.gpu_turbo,
                 self.verticalSlider_2, self.coolboost_checkbox, self.exit_button]
        for first, second in zip(order, order[1:]):
            window.setTabOrder(first, second)
