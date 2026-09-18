"""Passive telemetry presentation. No hardware access, polling, or control requests."""

import time

from PyQt6 import QtCore, QtGui, QtWidgets

from predator_sense.core.profiles import FAN_RPM_MAX
from predator_sense.font_config import font_numeric, font_ui
from predator_sense.ui.theme import THEME


MODE_LABELS = {"auto": "AUTO", "firmware_auto": "AUTO", "manual": "MANUAL", "turbo": "TURBO"}


def set_text(label, text):
    if label.text() != text:
        label.setText(text)


def set_role(widget, role):
    if widget.property("role") != role:
        widget.setProperty("role", role)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()


def label(text, *, role="", size=10, bold=False):
    widget = QtWidgets.QLabel(text)
    widget.setTextFormat(QtCore.Qt.TextFormat.PlainText)
    widget.setFont(font_ui(size, bold=bold))
    widget.setProperty("role", role)
    return widget


class Sparkline(QtWidgets.QWidget):
    def __init__(self, field, ceiling, parent=None):
        super().__init__(parent)
        self.field, self.ceiling = field, ceiling
        self.points = ()
        self.end = 0.0
        self.setMinimumHeight(48)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self.setAccessibleName(field.replace("_", " ") + " history, last 60 seconds")
        self.setToolTip("Last 60 seconds · gaps indicate missing or stale readings · no smoothing")

    def set_history(self, history, now):
        points = tuple((s.monotonic_timestamp, s.value(self.field)) for s in history
                       if now - 60 <= s.monotonic_timestamp <= now)
        if points != self.points or now != self.end:
            self.points, self.end = points, now
            self.update()

    def segments(self):
        """Preserve gaps, including daemon outages; never connect missing samples."""
        segments, current = [], []
        previous = None
        for timestamp, value in self.points:
            if value is None or (previous is not None and timestamp - previous > 2.5):
                if current:
                    segments.append(current)
                current = []
            if value is not None:
                current.append((timestamp, value))
            previous = timestamp
        if current:
            segments.append(current)
        return segments

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        bounds = QtCore.QRectF(self.rect()).adjusted(2, 4, -2, -4)
        painter.setPen(QtGui.QPen(QtGui.QColor(THEME.border), 1))
        for fraction in (0.0, 0.5, 1.0):
            y = bounds.top() + bounds.height() * fraction
            painter.drawLine(QtCore.QPointF(bounds.left(), y), QtCore.QPointF(bounds.right(), y))
        segments = self.segments()
        if not segments:
            painter.setPen(QtGui.QColor(THEME.muted))
            painter.setFont(font_ui(9))
            painter.drawText(bounds, QtCore.Qt.AlignmentFlag.AlignCenter, "Awaiting readings")
            return
        color = QtGui.QColor(THEME.accent if self.field.endswith("temp_c") else THEME.muted)
        for segment in segments:
            path = QtGui.QPainterPath()
            coordinates = [QtCore.QPointF(
                bounds.right() - (self.end - timestamp) / 60 * bounds.width(),
                bounds.bottom() - min(value / self.ceiling, 1.0) * bounds.height(),
            ) for timestamp, value in segment]
            path.moveTo(coordinates[0])
            for point in coordinates[1:]:
                path.lineTo(point)
            area = QtGui.QPainterPath(path)
            area.lineTo(coordinates[-1].x(), bounds.bottom())
            area.lineTo(coordinates[0].x(), bounds.bottom())
            area.closeSubpath()
            fill = QtGui.QColor(color)
            fill.setAlpha(15)
            painter.fillPath(area, fill)
            painter.setPen(QtGui.QPen(color, 1.6))
            painter.drawPath(path)
            painter.setBrush(color)
            painter.drawEllipse(coordinates[-1], 2, 2)


class TelemetryCard(QtWidgets.QFrame):
    def __init__(self, channel):
        super().__init__()
        self.channel = channel
        self.setProperty("panel", True)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(8)
        heading = QtWidgets.QHBoxLayout()
        heading.addWidget(label(channel.upper(), size=13, bold=True))
        heading.addWidget(label("PROCESSOR" if channel == "cpu" else "GRAPHICS", role="muted", size=9))
        heading.addStretch()
        self.mode = label("—", role="accent", size=9, bold=True)
        heading.addWidget(self.mode)
        layout.addLayout(heading)
        readings = QtWidgets.QHBoxLayout()
        self.temperature = label("—")
        self.temperature.setFont(font_numeric(38, bold=True))
        self.temperature.setMinimumWidth(self.temperature.fontMetrics().horizontalAdvance("125°C"))
        self.temperature.setAccessibleName(f"{channel.upper()} temperature unavailable")
        readings.addWidget(self.temperature)
        readings.addStretch()
        fan = QtWidgets.QVBoxLayout()
        self.rpm = label("—")
        self.rpm.setFont(font_numeric(19, bold=True))
        self.rpm.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        fan.addWidget(self.rpm)
        unit = label("RPM · CANDIDATE", role="muted", size=8)
        unit.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        unit.setToolTip("G3-572 fan-speed word. Absolute RPM units still require physical validation.")
        fan.addWidget(unit)
        readings.addLayout(fan)
        layout.addLayout(readings)
        self.availability = label("Waiting for telemetry", role="muted", size=9)
        self.availability.setWordWrap(True)
        layout.addWidget(self.availability)
        self.graphs = []
        for field, title, maximum, unit in (
            (channel + "_temp_c", "TEMPERATURE", 125, "°C"),
            (channel + "_fan_rpm", "FAN SPEED", FAN_RPM_MAX, "RPM"),
        ):
            legend = QtWidgets.QHBoxLayout()
            legend.addWidget(label(title, role="eyebrow", size=8, bold=True))
            legend.addStretch()
            legend.addWidget(label(f"0–{maximum} {unit}", role="muted", size=8))
            layout.addLayout(legend)
            graph = Sparkline(field, maximum)
            self.graphs.append(graph)
            layout.addWidget(graph, 1)
        time_axis = QtWidgets.QHBoxLayout()
        time_axis.addWidget(label("−60s", role="muted", size=8))
        time_axis.addStretch()
        time_axis.addWidget(label("NOW", role="muted", size=8))
        layout.addLayout(time_axis)

    def render(self, snapshot, history):
        temp, rpm = snapshot.value(self.channel + "_temp_c"), snapshot.value(self.channel + "_fan_rpm")
        set_text(self.temperature, "—" if temp is None else f"{temp:.0f}°C")
        set_text(self.rpm, "—" if rpm is None else f"{rpm:,}")
        set_text(self.mode, MODE_LABELS.get(snapshot.value(self.channel + "_mode"), "UNKNOWN"))
        set_role(self.mode, "accent" if snapshot.value(self.channel + "_mode") else "muted")
        messages = []
        details = []
        for field, title in ((self.channel + "_temp_c", "Temperature"), (self.channel + "_fan_rpm", "RPM")):
            reading = snapshot.reading(field)
            if snapshot.value(field) is None:
                status = {"backend_disconnected": "offline", "sensor_failed": "read failed"}.get(
                    reading.status.value, reading.status.value.replace("_", " ")
                )
                messages.append(f"{title}: {status}")
            details.append(f"{title}: {reading.source or 'No source'}\n{reading.error}")
        set_text(self.availability, " · ".join(messages) if messages else "LIVE / 1 Hz")
        self.availability.setToolTip("\n".join(details))
        temperature_description = temp if temp is not None else "unavailable"
        self.temperature.setAccessibleName(f"{self.channel.upper()} temperature {temperature_description}")
        self.rpm.setAccessibleName(f"{self.channel.upper()} candidate RPM {rpm if rpm is not None else 'unavailable'}")
        for graph in self.graphs:
            graph.set_history(history, time.monotonic())


class CoolBoostSwitch(QtWidgets.QCheckBox):
    """Native checkbox semantics/accessibility, with a compact switch indicator."""
    def sizeHint(self):
        return QtCore.QSize(self.fontMetrics().horizontalAdvance(self.text()) + 60,
                            max(32, self.fontMetrics().height() + 12))

    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        track = QtCore.QRectF(5, self.height() / 2 - 9, 34, 18)
        checked = self.checkState() == QtCore.Qt.CheckState.Checked
        unknown = self.checkState() == QtCore.Qt.CheckState.PartiallyChecked
        background = THEME.accent if checked and self.isEnabled() else THEME.border
        painter.setPen(QtGui.QPen(QtGui.QColor(THEME.accent_hover if self.underMouse() else background), 1))
        painter.setBrush(QtGui.QColor(background))
        painter.drawRoundedRect(track, 9, 9)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(THEME.text if self.isEnabled() else THEME.disabled))
        position = 15 if unknown else 23 if checked else 7
        painter.drawEllipse(QtCore.QRectF(position, self.height() / 2 - 7, 14, 14))
        painter.setPen(QtGui.QColor(THEME.text if self.isEnabled() else THEME.disabled))
        painter.setFont(self.font())
        painter.drawText(self.rect().adjusted(50, 0, 0, 0), QtCore.Qt.AlignmentFlag.AlignVCenter, self.text())
        if self.hasFocus():
            painter.setPen(QtGui.QPen(QtGui.QColor(THEME.accent_hover), 1, QtCore.Qt.PenStyle.DashLine))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))
