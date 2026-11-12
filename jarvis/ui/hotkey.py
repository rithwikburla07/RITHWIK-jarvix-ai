"""A small, allowlisted Windows push-to-talk hotkey registration."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QTimer

CHORDS = {
    "Ctrl+Shift+Space": (0x0002 | 0x0004, 0x20),
    "Ctrl+Alt+Space": (0x0002 | 0x0001, 0x20),
    "Ctrl+Shift+F9": (0x0002 | 0x0004, 0x78),
    "Ctrl+Alt+F9": (0x0002 | 0x0001, 0x78),
}
ID = 0x4A61


class Hotkey(QAbstractNativeEventFilter):
    def __init__(self, handle, action):
        super().__init__()
        self.handle, self.action = handle, action
        self.registered = False
        self.user = ctypes.WinDLL("user32") if os.name == "nt" else None
        if self.user is not None:
            self.user.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
            self.user.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)

    def register(self, name):
        self.close()
        if self.user is None or name not in CHORDS:
            return False
        modifiers, key = CHORDS[name]
        if not self.user.RegisterHotKey(self.handle, ID, modifiers | 0x4000, key):
            return False
        self.registered = True
        QCoreApplication.instance().installNativeEventFilter(self)
        return True

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) in {b"windows_generic_MSG", b"windows_dispatcher_MSG"}:
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam == ID and msg.hWnd == self.handle:
                QTimer.singleShot(0, self.action)
                return True, 0
        return False, 0

    def close(self):
        if self.registered:
            self.user.UnregisterHotKey(self.handle, ID)
            QCoreApplication.instance().removeNativeEventFilter(self)
            self.registered = False
