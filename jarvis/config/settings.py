"""Project settings stored in a bounded, atomically replaced JSON file."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

DEFAULTS = {
    "title": "Jarvix Personal AI",
    "greeting": "",
    "reduced_motion": False,
    "theme": "cyan",
    "background": "",
    "search_folders": [],
    "show_orb": False,
    "orb_x": None,
    "orb_y": None,
    "close_to_tray": False,
    "notifications": True,
    "privacy": False,
    "language": "en",
    "hotkey": "Ctrl+Shift+Space",
    "voice_provider": "off",
    "vosk_model": "",
    "wake_word": False,
    "speech": False,
    "speech_rate": 175,
    "speech_volume": 75,
    "speech_voice": "",
    "music_folder": "",
    "brain_endpoint": "",
    "brain_model": "",
    "brain_enabled": False,
    "study_url": "",
    "busy_reply": "",
    "busy_enabled": False,
}


def read_json(path: Path, maximum: int = 256_000):
    with path.open("rb") as handle:
        data = handle.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("Configuration file is too large.")
    return json.loads(data.decode("utf-8"))


def write_json(path: Path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="." + path.stem + "-", dir=path.parent) as folder:
        temporary = Path(folder) / path.name
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)


def _valid(key, value):
    if key not in DEFAULTS:
        return False
    default = DEFAULTS[key]
    if default is None:
        return value is None or (type(value) is int and -100_000 <= value <= 100_000)
    if type(value) is not type(default):
        return False
    if key == "search_folders":
        return len(value) <= 8 and all(isinstance(item, str) and 0 < len(item) <= 260 and
                                       not any(ord(char) < 32 for char in item) for item in value)
    if key == "speech_rate":
        return 80 <= value <= 350
    if key == "speech_volume":
        return 0 <= value <= 100
    if key == "language":
        return value in {"en", "hi", "ru", "uk", "pt", "tr", "it", "de"}
    if key == "voice_provider":
        return value in {"off", "vosk", "google"}
    if key == "theme":
        return value in {"cyan", "gold"}
    return not isinstance(value, str) or (
        len(value) <= 2_000 and not any(ord(c) < 32 for c in value)
    )


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = path
        self._lock = threading.RLock()
        self.problem = ""
        self._values = dict(DEFAULTS)
        shipped = Path(__file__).resolve().parents[2] / 'data' / 'settings.json'
        try:
            defaults = read_json(shipped)
            self._values.update({key: value for key, value in defaults.items() if _valid(key, value)})
        except (OSError, ValueError, AttributeError):
            pass
        if path is not None:
            try:
                data = read_json(path)
                if not isinstance(data, dict):
                    raise ValueError("Expected a settings object.")
                self._values.update({k: v for k, v in data.items() if _valid(k, v)})
            except FileNotFoundError:
                pass
            except (OSError, ValueError):
                self.problem = "Settings could not be read. Safe defaults are active."

    def get(self, key):
        with self._lock:
            value = self._values[key]
            return list(value) if isinstance(value, list) else value

    def update(self, **changes) -> None:
        if any(not _valid(key, value) for key, value in changes.items()):
            raise ValueError("One or more settings are invalid.")
        with self._lock:
            updated = {**self._values, **changes}
            if self.path is not None:
                write_json(self.path, updated)
            self._values = updated
            self.problem = ""
