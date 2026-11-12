"""Bounded, approved local music discovery for a Qt playback client."""
from __future__ import annotations

import os

from ..core.intents import Reply
from ..core.safety import PathEnv, default_is_reparse
from ..system.desktop import MUSIC_SUFFIXES, local_directory

MAX_SCAN = 3500
MAX_TRACKS = 300


def tracks_in(folder):
    env = PathEnv.from_environ()
    if not folder or not local_directory(folder, env):
        raise ValueError("Choose an accessible local music folder.")
    pending, tracks = [(folder, 0)], []
    scanned = 0
    while pending and scanned < MAX_SCAN and len(tracks) < MAX_TRACKS:
        path, depth = pending.pop()
        if not local_directory(path, env):
            continue
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    scanned += 1
                    if scanned > MAX_SCAN or len(tracks) >= MAX_TRACKS:
                        break
                    if default_is_reparse(entry.path):
                        continue
                    if depth < 4 and entry.is_dir(follow_symlinks=False):
                        pending.append((entry.path, depth + 1))
                    if os.path.splitext(entry.name)[1].casefold() in MUSIC_SUFFIXES and entry.is_file(follow_symlinks=False):
                        tracks.append(entry.path)
        except OSError:
            continue
    return sorted(tracks, key=lambda path: os.path.basename(path).casefold())


class Music:
    def __init__(self, settings):
        self.settings = settings
        self.available = False
        self.matches = []

    def handle(self, intent, _now):
        if not self.available:
            return Reply("unavailable", "Local playback needs the desktop window.", "music.unavailable"), None
        action = intent.name.removeprefix("music_")
        if action not in {"play", "select", "scan", "stop", "pause", "resume", "next", "previous"}:
            return Reply("refused", "Unsupported music control.", "music.refused"), None
        data = {"action": action}
        if action == "select":
            index = intent.get("index")
            if type(index) is not int or not 0 <= index < len(self.matches):
                return Reply("info", "Select a local track first.", "music.selection"), None
            data["index"] = index
        if action in {"play", "scan"}:
            try:
                tracks = tracks_in(self.settings.get("music_folder"))
            except ValueError as exc:
                return Reply("unavailable", str(exc), "music.unavailable"), None
            query = intent.get("query", "").strip()
            terms = query.casefold().split() if query.casefold() not in {"music", "local music", "playlist"} else []
            matches = [path for path in tracks if all(term in os.path.basename(path).casefold() for term in terms)]
            if action == "play" and not matches:
                return Reply("info", "No local track matched. You can choose Search YouTube on the Music page.",
                             "music.no_match", {"query": query}), None
            self.matches = matches
            data.update(tracks=matches, query=query)
        return Reply("info", "Playback request received." if action != "scan" else f"Found {len(data['tracks'])} tracks.",
                     "music.request", data), None
