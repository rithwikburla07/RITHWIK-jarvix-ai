"""Original fictional armour visualization with view controls."""
from math import cos, radians, sin

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QLabel, QSlider, QVBoxLayout, QWidget

PARTS = {
    "Helmet": (0, -2.7, .57, .48, .41),
    "Torso": (0, -1.5, 1.12, .88, .52),
    "Shoulders": (0, -1.96, 1.83, .34, .57),
    "Left arm": (-1.37, -.75, .4, 1.03, .42),
    "Right arm": (1.37, -.75, .4, 1.03, .42),
    "Hip": (0, -.37, .83, .4, .49),
    "Left leg": (-.51, 1.14, .43, 1.26, .47),
    "Right leg": (.51, 1.14, .43, 1.26, .47),
}


class Armour(QWidget):
    def __init__(self):
        super().__init__()
        self.angle, self.zoom = 23, 100
        self.selected = "Torso"
        self.blueprint = False
        self.static = False
        self.rotate = True
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.spin)
        self.setMinimumHeight(300)

    def showEvent(self, event):
        self.sync()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def sync(self):
        if self.isVisible() and self.rotate and not self.static and not self.blueprint:
            self.timer.start()
        else:
            self.timer.stop()

    def spin(self):
        self.angle = (self.angle + .65) % 360
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0d1825" if self.blueprint else "#09131c"))
        painter.setPen(QPen(QColor("#182f42"), 1))
        for x in range(self.width() // 2 % 26, self.width(), 26):
            painter.drawLine(x, 0, x, self.height())
        for y in range(self.height() // 2 % 26, self.height(), 26):
            painter.drawLine(0, y, self.width(), y)
        theta = radians(0 if self.blueprint else self.angle)
        scale = min(self.width() / 7.5, self.height() / 6.7) * self.zoom / 100
        center = QPointF(self.rect().center()) + QPointF(0, 13)
        for name, (cx, cy, dx, dy, dz) in PARTS.items():
            verts = []
            for index in range(8):
                x = cx + (dx if index & 1 else -dx)
                y = cy + (dy if index & 2 else -dy)
                z = dz if index & 4 else -dz
                world_x = x * cos(theta) + z * sin(theta)
                world_z = z * cos(theta) - x * sin(theta)
                verts.append(center + QPointF(world_x * scale, (y + world_z * .16) * scale))
            color = "#ffc857" if name == self.selected else "#67d8ec"
            painter.setPen(QPen(QColor(color), 2 if name == self.selected else 1))
            for i in range(8):
                for side in (1, 2, 4):
                    if not i & side:
                        painter.drawLine(verts[i], verts[i | side])
        painter.end()


class BattlePanel(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        title = QLabel("Armour visualization")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(QLabel("Entertainment and visualization only."))
        self.armour = Armour()
        layout.addWidget(self.armour, 1)
        controls = QFormLayout()
        parts = QComboBox()
        parts.addItems(PARTS)
        parts.setCurrentText(self.armour.selected)
        parts.currentTextChanged.connect(lambda name: self.update_view(selected=name))
        controls.addRow("Component", parts)
        self.dimensions = QLabel()
        controls.addRow("Illustrative dimensions", self.dimensions)
        for key, label, lo, hi, value in (("angle", "Rotation", 0, 360, 23), ("zoom", "Zoom", 65, 135, 100)):
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(lo, hi)
            slider.setValue(value)
            slider.valueChanged.connect(lambda amount, name=key: self.update_view(**{name: amount}))
            controls.addRow(label, slider)
        blueprint = QCheckBox("Blueprint view")
        blueprint.toggled.connect(lambda value: self.update_view(blueprint=value))
        controls.addRow(blueprint)
        rotate = QCheckBox("Rotate")
        rotate.setChecked(True)
        rotate.toggled.connect(lambda value: self.update_view(rotate=value))
        controls.addRow(rotate)
        layout.addLayout(controls)
        self.update_view()

    def update_view(self, **values):
        for key, value in values.items():
            setattr(self.armour, key, value)
        parts = PARTS[self.armour.selected][2:]
        self.dimensions.setText(" × ".join(f"{2 * value:.2f}" for value in parts) + " fictional units")
        self.armour.sync()
        self.armour.update()
