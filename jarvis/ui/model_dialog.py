"""Visible, cancellable model download dialog."""
from threading import Event

from PySide6.QtCore import QThreadPool, Signal
from PySide6.QtWidgets import QDialog, QFormLayout, QLabel, QLineEdit, QProgressBar, QPushButton

from ..voice.models import download
from .voice_controls import VoiceTask


class ModelDialog(QDialog):
    progress = Signal(int, int)
    result = Signal(object, object, str)

    def __init__(self, parent, profile, selected):
        super().__init__(parent)
        self.setWindowTitle("Download an offline voice model")
        self.setMinimumWidth(480)
        self.profile, self.selected = profile, selected
        self.stop = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        form = QFormLayout(self)
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://alphacephei.com/vosk/models/…")
        self.checksum = QLineEdit()
        self.checksum.setMaxLength(64)
        self.bar = QProgressBar()
        self.status = QLabel("Use an official small Vosk model and its independently verified SHA-256.")
        self.status.setWordWrap(True)
        self.go = QPushButton("Download and verify")
        self.go.clicked.connect(self.begin)
        self.cancel = QPushButton("Cancel download")
        self.cancel.clicked.connect(self.cancel_download)
        form.addRow("Model ZIP", self.url)
        form.addRow("Expected SHA-256", self.checksum)
        for widget in (self.bar, self.status, self.go, self.cancel):
            form.addRow(widget)
        self.progress.connect(self.update_progress)
        self.result.connect(self.finished_download)

    def begin(self):
        if self.stop is not None:
            return
        if self.profile is None:
            self.status.setText("The local Jarvix profile is unavailable.")
            return
        self.stop = token = Event()
        url, checksum = self.url.text().strip(), self.checksum.text().strip()
        self.status.setText("Downloading and verifying…")
        self.go.setEnabled(False)
        self.bar.setRange(0, 0)
        self.pool.start(VoiceTask(lambda: download(self.profile, url, checksum, token, self.progress.emit),
                                  self.result, token))

    def update_progress(self, done, total):
        if total:
            self.bar.setRange(0, 100)
            self.bar.setValue(min(100, round(done * 100 / total)))

    def finished_download(self, token, result, error):
        if token is not self.stop:
            return
        self.stop = None
        self.go.setEnabled(True)
        self.bar.setRange(0, 100)
        self.bar.setValue(0 if error else 100)
        if error:
            self.status.setText(error)
        elif result:
            self.selected(str(result))
            self.accept()

    def cancel_download(self):
        if self.stop is None:
            self.reject()
        else:
            self.stop.set()
            self.status.setText("Cancelling…")

    def reject(self):
        if self.stop is not None:
            self.cancel_download()
        else:
            super().reject()
