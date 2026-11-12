"""Painted dashboard, live probes and the optional desktop orb."""
from __future__ import annotations

from datetime import datetime, timedelta
from math import cos, pi, sin

from PySide6.QtCore import QPoint, QPointF, QRectF, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QProgressBar, QVBoxLayout, QWidget,
)

from ..tasks.service import describe

COLORS = {
    "Idle": "#5ee9fb", "Listening": "#85f4cf", "Thinking": "#b6a3ff",
    "Executing": "#79ddff", "Speaking": "#a6e9ab", "Success": "#78ecc0",
    "Error": "#ff8393", "Paused": "#ffd07b",
}


def paint_reactor(painter, center, radius, color, phase):
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    glow = QRadialGradient(center, radius)
    faint = QColor(color)
    faint.setAlpha(45)
    glow.setColorAt(0, faint)
    faint.setAlpha(18)
    glow.setColorAt(.66, faint)
    glow.setColorAt(1, Qt.GlobalColor.transparent)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(glow)
    painter.drawEllipse(center, radius, radius)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for scale, alpha in ((.91, 42), (.76, 120), (.63, 56), (.38, 145)):
        ring = QColor(color)
        ring.setAlpha(alpha)
        painter.setPen(QPen(ring, 1.0))
        painter.drawEllipse(center, radius * scale, radius * scale)
    for index in range(36):
        angle = 2 * pi * index / 36
        inner = .83 if index % 3 == 0 else .87
        ring = QColor(color)
        ring.setAlpha(165 if index % 3 == 0 else 75)
        painter.setPen(QPen(ring, 1.4))
        start = center + QPointF(cos(angle), sin(angle)) * (radius * inner)
        end = center + QPointF(cos(angle), sin(angle)) * (radius * .91)
        painter.drawLine(start, end)
    arc_pen = QPen(color)
    arc_pen.setWidthF(max(1.0, radius * .023))
    arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(arc_pen)
    for scale, offset, span in ((.73, 0, 71), (.53, 38, 45), (.27, 91, 92)):
        edge = radius * scale
        circle = QRectF(center.x() - edge, center.y() - edge, edge * 2, edge * 2)
        for index in range(3):
            painter.drawArc(circle, int((phase + offset + index * 120) * 16), span * 16)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(center, radius * .074, radius * .074)


def reactor_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    paint_reactor(painter, QPointF(32, 32), 31, QColor(COLORS["Idle"]), 0)
    painter.end()
    return QIcon(pixmap)


class Reactor(QWidget):
    def __init__(self, compact=False):
        super().__init__()
        self.compact = compact
        self.state = "Idle"
        self.phase = 0.0
        self.level = 0.0
        self.samples = [0.0] * 30
        self.static = False
        self.accent = COLORS["Idle"]
        self.setMinimumSize(*( (56, 56) if compact else (156, 154) ))
        self.setAccessibleName("Jarvix status")
        self.timer = QTimer(self)
        self.timer.setInterval(34)
        self.timer.timeout.connect(self.advance)

    def showEvent(self, event):
        self._sync_timer()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def _sync_timer(self):
        animate = self.isVisible() and not self.static and self.state != "Paused"
        if animate and not self.timer.isActive():
            self.timer.start()
        elif not animate:
            self.timer.stop()

    def advance(self):
        self.phase = (self.phase + (2.2 if self.state != "Idle" else .55)) % 360
        self.update()

    def set_state(self, state):
        self.state = state
        if state != "Listening":
            self.level = 0.0
            self.samples = [0.0] * len(self.samples)
        self.setAccessibleDescription(state)
        self._sync_timer()
        self.update()

    def set_level(self, level):
        self.level = max(0.0, min(float(level), 1.0))
        self.samples = [*self.samples[1:], self.level]
        self.update()

    def set_static(self, static):
        self.static = static
        self._sync_timer()
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        rect = self.rect()
        center = QPointF(rect.center()) - QPointF(0, 9 if not self.compact else 0)
        radius = min(rect.width(), rect.height() - (25 if not self.compact else 0)) * .48
        color = QColor(self.accent if self.state == "Idle" else COLORS.get(self.state, self.accent))
        paint_reactor(painter, center, radius, color, self.phase)
        if not self.compact:
            width = min(rect.width() - 18, 170)
            gap = width / len(self.samples)
            start = (rect.width() - width) / 2
            y = rect.bottom() - 8
            waveform_pen = QPen(color)
            waveform_pen.setWidthF(1.4)
            waveform_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(waveform_pen)
            for index, sample in enumerate(self.samples):
                x = start + index * gap
                height = max(.7, sample * 10)
                painter.drawLine(QPointF(x, y - height), QPointF(x, y + height))
        painter.end()


class FloatingOrb(Reactor):
    restore = Signal()
    speak = Signal()
    toggle_pause = Signal()
    position_saved = Signal(int, int)

    def __init__(self):
        super().__init__(compact=True)
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(66, 66)
        self.setToolTip("Click to open Jarvix. Drag to move. Right-click for controls.")
        self._origin = None
        self._moved = False

    def place(self, x=None, y=None):
        primary = QGuiApplication.primaryScreen()
        if primary is None:
            return
        area = primary.availableGeometry()
        point = (QPoint(x, y) if x is not None and y is not None
                 else QPoint(area.right() - 94, area.bottom() - 100))
        self._clamp(point)

    def _clamp(self, point):
        screen = QGuiApplication.screenAt(point + QPoint(self.width() // 2, self.height() // 2))
        screen = screen or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(max(area.left(), min(point.x(), area.right() - self.width() + 1)),
                      max(area.top(), min(point.y(), area.bottom() - self.height() + 1)))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.globalPosition().toPoint() - self.pos()
            self._moved = False

    def mouseMoveEvent(self, event):
        if self._origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            target = event.globalPosition().toPoint() - self._origin
            if self._moved or (target - self.pos()).manhattanLength() > 4:
                self._moved = True
                self._clamp(target)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._origin is not None:
            if self._moved:
                self.position_saved.emit(self.x(), self.y())
            else:
                self.restore.emit()
            self._origin = None

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("Open Jarvix", self.restore.emit)
        menu.addAction("Push to talk", self.speak.emit)
        label = "Resume Jarvix" if self.state == "Paused" else "Pause Jarvix"
        menu.addAction(label, self.toggle_pause.emit)
        menu.exec(event.globalPos())


class Meter(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName("meter")
        box = QVBoxLayout(self)
        box.setContentsMargins(10, 8, 10, 8)
        header = QHBoxLayout()
        label = QLabel(title)
        label.setObjectName("caption")
        self.value = QLabel("—")
        self.value.setObjectName("metric")
        header.addWidget(label)
        header.addStretch()
        header.addWidget(self.value)
        self.bar = QProgressBar()
        self.bar.setAccessibleName(title)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(4)
        self.detail = QLabel("Waiting for data")
        self.detail.setObjectName("note")
        box.addLayout(header)
        box.addWidget(self.bar)
        box.addWidget(self.detail)

    def set_value(self, value, detail=""):
        self.value.setText("—" if value is None else f"{value:.0f}%")
        self.bar.setValue(0 if value is None else round(value))
        self.detail.setText(detail or ("Unavailable" if value is None else "In use"))


class Dashboard(QFrame):
    visibility_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.image = None
        self.setObjectName("panel")
        layout = QHBoxLayout(self)
        reactor_column = QVBoxLayout()
        self.reactor = Reactor()
        self.voice = QLabel("Microphone off")
        self.voice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.voice.setObjectName("note")
        reactor_column.addWidget(self.reactor, 1)
        reactor_column.addWidget(self.voice)
        layout.addLayout(reactor_column, 4)
        meters = QGridLayout()
        self.meters = {}
        for index, title in enumerate(("CPU", "Memory", "Battery", "Storage")):
            meter = Meter(title)
            self.meters[title] = meter
            meters.addWidget(meter, index // 2, index % 2)
        layout.addLayout(meters, 7)

    def showEvent(self, event):
        super().showEvent(event)
        self.visibility_changed.emit(True)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.visibility_changed.emit(False)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.image is not None and not self.image.isNull():
            painter = QPainter(self)
            painter.setOpacity(.22)
            painter.drawImage(self.rect().adjusted(2, 2, -2, -2), self.image)
            painter.end()

    def show_metrics(self, metrics):
        battery = metrics.get("battery")
        storage = metrics.get("storage")
        self.meters["CPU"].set_value(metrics.get("cpu"), "Processor load")
        self.meters["Memory"].set_value(metrics.get("ram"), "Physical memory")
        self.meters["Battery"].set_value(
            battery["percent"] if battery else None,
            ("Plugged in" if battery["plugged"] else "On battery") if battery else "No reading",
        )
        self.meters["Storage"].set_value(
            storage["percent"] if storage else None,
            f"{storage['free_gb']:.1f} GB free" if storage else "",
        )


class Probe(QRunnable):
    def __init__(self, engine, signal):
        super().__init__()
        self.engine, self.signal = engine, signal

    def run(self):
        result = {"metrics": {}, "tasks": [], "reminders": [], "problems": []}
        system = getattr(self.engine, "system", None)
        if system is not None:
            try:
                result["metrics"] = system.sysinfo.snapshot()
            except Exception as exc:
                result["problems"].append(f"System metrics unavailable ({type(exc).__name__}).")
        tasks = getattr(self.engine, "tasks", None)
        if tasks is not None:
            try:
                now = datetime.now()
                repo = tasks.repo
                tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time())
                today = repo.items_between(None, tomorrow, type_="task", limit=6)
                result["tasks"] = [describe(item, now) for item in today]
                items = repo.items_between(now, now + timedelta(days=7), type_="reminder", limit=6)
                result["reminders"] = [describe(item, now) for item in items]
            except Exception as exc:
                result["problems"].append(f"Task data unavailable ({type(exc).__name__}).")
        self.signal.emit(result)


class DashboardPoller:
    def __init__(self, parent, engine, signal):
        self.pool = QThreadPool(parent)
        self.pool.setMaxThreadCount(1)
        self.pool.setExpiryTimeout(4_000)
        self.engine, self.signal = engine, signal
        self.timer = QTimer(parent)
        self.timer.setInterval(4_000)
        self.timer.timeout.connect(self.refresh)

    def refresh(self):
        self.pool.tryStart(Probe(self.engine, self.signal))

    def start(self):
        self.refresh()
        self.timer.start()

    def stop(self):
        self.timer.stop()
        self.pool.waitForDone()
