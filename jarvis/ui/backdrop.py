"""Optional local dashboard image or silent background video."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QImageReader
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

from ..core.safety import PathEnv, check_mutation_path


class Backdrop(QObject):
    status = Signal(str)
    IMAGE = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
    VIDEO = {".mp4", ".webm", ".wmv"}

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self.player = self.sink = None
        self.static = False
        self.last = 0.
        panel.visibility_changed.connect(self.update_playback)

    def load(self, path, static):
        self.stop()
        self.static = static
        self.last = 0.
        self.panel.image = None
        self.panel.update()
        if not path:
            return
        file = Path(path)
        env = PathEnv.from_environ()
        try:
            if not file.is_absolute() or not check_mutation_path(str(file), [str(file.parent)], env).allowed:
                raise ValueError("Choose an approved local image or video.")
            extension = file.suffix.casefold()
            size = file.stat().st_size
            if extension in self.IMAGE and size <= 25_000_000:
                reader = QImageReader(str(file))
                source = reader.size()
                if not source.isValid() or source.width() * source.height() > 38_000_000:
                    raise ValueError("The image dimensions are unsupported.")
                reader.setScaledSize(source.scaled(QSize(1600, 900), Qt.AspectRatioMode.KeepAspectRatio))
                self.panel.image = reader.read()
                if self.panel.image.isNull():
                    raise ValueError("The image could not be read.")
                self.panel.update()
            elif extension in self.VIDEO and size <= 100_000_000:
                self.sink = QVideoSink(self)
                self.player = QMediaPlayer(self)
                self.player.setVideoSink(self.sink)
                self.player.setLoops(QMediaPlayer.Loops.Infinite)
                self.player.errorOccurred.connect(lambda _err, _detail: self.status.emit(
                    "Background video could not play. The static dashboard is available."
                ))
                self.sink.videoFrameChanged.connect(self.frame)
                self.player.setSource(QUrl.fromLocalFile(str(file)))
                self.update_playback(self.panel.isVisible())
            else:
                raise ValueError("Choose a supported image under 25 MB or video under 100 MB.")
        except (ValueError, OSError) as exc:
            self.status.emit(str(exc) if isinstance(exc, ValueError) else "The background file could not be opened.")

    def frame(self, frame):
        now = time.monotonic()
        if not frame.isValid() or now - self.last < .05 or frame.width() * frame.height() > 8_400_000:
            return
        image = frame.toImage()
        if not image.isNull():
            self.last = now
            self.panel.image = image
            self.panel.update()
            if self.static and self.player:
                self.player.pause()

    def update_playback(self, visible):
        if self.player:
            if visible and (not self.static or self.panel.image is None):
                self.player.play()
            else:
                self.player.pause()

    def stop(self):
        if self.player:
            self.player.stop()
            self.player.deleteLater()
            self.sink.deleteLater()
            self.player = self.sink = None
