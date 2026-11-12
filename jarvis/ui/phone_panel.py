"""Private-network pairing, review and transfer status."""
from __future__ import annotations

import json
import socket

from PySide6.QtCore import QTime, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QProgressBar, QSpinBox, QTimeEdit, QVBoxLayout, QWidget,
)

from .integration_panels import button, feedback, heading


class PhonePanel(QWidget):
    def __init__(self, link, submit):
        super().__init__()
        self.link, self.submit = link, submit
        self.invitation = ""
        self.device_rows = []
        self.jobs = []
        box = QVBoxLayout(self)
        heading(box, "Android companion")
        form = QFormLayout()
        local = QHBoxLayout()
        self.address = QLineEdit()
        self.address.setPlaceholderText("PC Wi-Fi IPv4 address")
        self.address.setMaxLength(15)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(8765)
        local.addWidget(self.address, 1)
        local.addWidget(self.port)
        local.addWidget(button("Start listener", lambda: submit(
            "phone_start", host=self.address.text(), port=self.port.value()
        )))
        local.addWidget(button("Stop listener", self.stop))
        form.addRow("Private address", local)
        devices = QHBoxLayout()
        self.devices = QComboBox()
        devices.addWidget(self.devices, 1)
        devices.addWidget(button("New pairing invitation", lambda: submit("phone_invite")))
        devices.addWidget(button("Revoke device", lambda: submit("phone_revoke", device=self.device())))
        form.addRow("Paired devices", devices)
        box.addLayout(form)
        body = QHBoxLayout()
        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr.setMinimumSize(155, 155)
        pairing = QVBoxLayout()
        pairing.addWidget(self.qr)
        self.copy = button("Copy invitation", lambda: QApplication.clipboard().setText(self.invitation))
        self.copy.setEnabled(False)
        pairing.addWidget(self.copy)
        body.addLayout(pairing)
        self.history = QListWidget()
        self.history.setAccessibleName("Phone actions and transfer history")
        body.addWidget(self.history, 1)
        box.addLayout(body, 1)
        row = QHBoxLayout()
        self.value = QLineEdit()
        self.value.setPlaceholderText("App name or international number")
        self.value.setMaxLength(60)
        row.addWidget(self.value, 1)
        row.addWidget(button("Open app", lambda: submit("phone_open_app", device=self.device(), app=self.value.text())))
        row.addWidget(button("Dial", lambda: submit("phone_dial", device=self.device(), number=self.value.text())))
        row.addWidget(button("Ring", lambda: submit("phone_ring", device=self.device())))
        box.addLayout(row)
        clock = QHBoxLayout()
        self.minutes = QSpinBox()
        self.minutes.setRange(1, 180)
        self.minutes.setValue(10)
        self.minutes.setSuffix(" min")
        self.alarm = QTimeEdit(QTime(7, 0))
        self.alarm.setDisplayFormat("HH:mm")
        for element in (
            self.minutes, button("Set timer", lambda: submit(
                "phone_timer", device=self.device(), minutes=self.minutes.value())),
            self.alarm, button("Set alarm", lambda: submit(
                "phone_alarm", device=self.device(), hour=self.alarm.time().hour(),
                minute=self.alarm.time().minute())), button("Offer file", self.offer),
            button("Cancel selected transfer", self.cancel),
        ):
            clock.addWidget(element)
        box.addLayout(clock)
        self.progress = QProgressBar()
        self.progress.hide()
        box.addWidget(self.progress)
        self.status = feedback()
        self.status.setText("Listener off. Activate it only when you’re ready to pair on private Wi-Fi.")
        box.addWidget(self.status)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(1000)
        self.refresh_timer.timeout.connect(self.refresh)
        self.invite_timer = QTimer(self)
        self.invite_timer.setSingleShot(True)
        self.invite_timer.timeout.connect(self.clear_invitation)

    def showEvent(self, event):
        self.refresh()
        self.refresh_timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.refresh_timer.stop()
        super().hideEvent(event)

    def device(self):
        return self.devices.currentData()

    def refresh(self):
        if not self.link:
            return
        data = self.link.view()
        if data["devices"] != self.device_rows:
            selected = self.device()
            self.device_rows = data["devices"]
            self.devices.clear()
            for row in self.device_rows:
                self.devices.addItem(row["name"] + (" · connected" if row["online"] and data["active"] else ""), row["id"])
            if selected:
                index = self.devices.findData(selected)
                self.devices.setCurrentIndex(max(index, 0))
        if data["jobs"] != self.jobs:
            row = self.history.currentRow()
            self.jobs = data["jobs"]
            self.history.clear()
            for item in self.jobs:
                args = item["args"]
                title = args.get("name", item["action"].replace("_", " "))
                if item["action"] == "transfer":
                    title += f" · {item['sent']:,}/{args['size']:,} bytes"
                self.history.addItem(f"{title} · {item['status']}")
            if row >= 0 and self.jobs:
                self.history.setCurrentRow(min(row, len(self.jobs) - 1))
        prep = data["progress"]
        self.progress.setVisible(prep is not None)
        if prep is not None:
            done, total = prep
            self.progress.setFormat("Preparing file · %p%")
            self.progress.setValue(round(done * 100 / total) if total else 100)

    def stop(self):
        if self.link:
            self.link.stop()
        self.clear_invitation()
        self.status.setText("Companion listener stopped.")
        self.refresh()

    def cancel(self):
        if self.link:
            self.link.prepare_stop.set()
            row = self.history.currentRow()
            if 0 <= row < len(self.jobs):
                self.link.pairing.cancel(self.jobs[row]["id"])
        self.refresh()

    def offer(self):
        path, _filter = QFileDialog.getOpenFileName(self, "Offer an approved file to the selected phone")
        if path:
            self.submit("phone_transfer", device=self.device(), path=path)

    def clear_invitation(self):
        self.invitation = ""
        self.copy.setEnabled(False)
        self.qr.clear()

    def reply(self, reply):
        self.status.setText(reply.text)
        if reply.key == "phone.invite":
            self.invitation = json.dumps(reply.data["invite"], separators=(",", ":"))
            self.copy.setEnabled(True)
            self.invite_timer.start(120_000)
            try:
                import qrcode
            except ImportError:
                self.qr.setText("Copy and paste invitation")
            else:
                qr = qrcode.QRCode(border=4)
                qr.add_data(self.invitation)
                qr.make(fit=True)
                grid = qr.get_matrix()
                scale = max(2, 220 // len(grid))
                pixmap = QPixmap(len(grid) * scale, len(grid) * scale)
                pixmap.fill(QColor("white"))
                painter = QPainter(pixmap)
                try:
                    for row, cells in enumerate(grid):
                        for col, black in enumerate(cells):
                            if black:
                                painter.fillRect(col * scale, row * scale, scale, scale, QColor("black"))
                finally:
                    painter.end()
                self.qr.setPixmap(pixmap)
        self.refresh()
