"""Approved Windows apps: resolve, launch, and gracefully close.

Rules:
  * Launches use argument lists. Nothing is passed through a shell.
  * The executable must be a known name from data/apps.json and must resolve to a
    real file whose basename matches. Locations come from fixed environment
    variables or read-only App Paths registry entries.
  * Closing sends a normal close request (taskkill without /F), so unsaved-work
    prompts still appear. Apps with closable=false are never closed by Jarvix.
"""
from __future__ import annotations

import json
import ntpath
import os
import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional

from ..core.safety import default_is_reparse

_EXE = re.compile(r"^[A-Za-z0-9_.\-]+\.exe$", re.I)
_ARG = re.compile(r"^[A-Za-z0-9:/_.\-=]{1,120}$")
_VAR = re.compile(r"%([^%]+)%")
CREATE_NEW_CONSOLE = 0x00000010


class AppError(Exception):
    pass


@dataclass(frozen=True)
class AppEntry:
    name: str
    aliases: tuple
    exe: str
    fallback: tuple = ()
    args: tuple = ()
    empty_console: bool = False
    closable: bool = True
    app_paths: str = ""
    approved_target: str = ""


def load_apps(path) -> list:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, list):
        raise ValueError("apps.json must be a list")
    out, seen = [], set()
    for e in raw:
        if not isinstance(e, dict):
            raise ValueError("each app must be an object")
        name = str(e.get("name", "")).strip()
        exe = str(e.get("exe", ""))
        args = tuple(e.get("args", []))
        if not name or name.casefold() in seen:
            raise ValueError(f"bad or duplicate app name: {name!r}")
        if not _EXE.match(exe):
            raise ValueError(f"{name}: exe must be a bare .exe file name")
        if not all(isinstance(a, str) and _ARG.match(a) for a in args):
            raise ValueError(f"{name}: arguments must be simple literals")
        seen.add(name.casefold())
        out.append(AppEntry(
            name=name,
            aliases=tuple(e.get("aliases", [])),
            exe=exe,
            fallback=tuple(e.get("fallback", [])),
            args=args,
            empty_console=bool(e.get("empty_console", False)),
            closable=bool(e.get("closable", True)),
            app_paths=str(e.get("app_paths", "")),
        ))
    return out


def _app_paths_lookup(exe: str) -> Optional[str]:
    """Read-only: HKLM/HKCU App Paths default value. Never writes the registry."""
    try:
        import winreg  # Windows only
    except ImportError:
        return None
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                val, _ = winreg.QueryValueEx(k, "")
                if isinstance(val, str) and val.strip():
                    return val.strip().strip('"')
        except OSError:
            continue
    return None


class AppRegistry:
    def __init__(self, entries, *, env=None, isfile: Callable[[str], bool] = os.path.isfile,
                 app_path_lookup: Callable[[str], Optional[str]] = _app_paths_lookup,
                 popen=subprocess.Popen, is_windows: bool = (os.name == "nt")):
        self.entries = list(entries)
        self.env = dict(os.environ) if env is None else dict(env)
        self.isfile = isfile
        self.app_path_lookup = app_path_lookup
        self.popen = popen
        self.is_windows = is_windows

    def find(self, query: str) -> Optional[AppEntry]:
        q = (query or "").strip().casefold()
        if not q:
            return None
        for e in self.entries:
            if e.name.casefold() == q or any(a.casefold() == q for a in e.aliases):
                return e
        return None

    def expand(self, text: str) -> Optional[str]:
        def sub(m):
            return self.env.get(m.group(1)) or self.env.get(m.group(1).upper()) or m.group(0)
        out = _VAR.sub(sub, text)
        return None if "%" in out else out

    def resolve_exe(self, e: AppEntry) -> Optional[str]:
        candidates = [self.expand(p) for p in e.fallback]
        if e.app_paths:
            candidates.append(self.app_path_lookup(e.app_paths))
        for c in candidates:
            if not c or not ntpath.isabs(c) or c.startswith("\\\\"):
                continue
            if ".." in c.replace("/", "\\").split("\\"):
                continue
            if ntpath.basename(c).casefold() != e.exe.casefold():
                continue
            if e.approved_target and (
                ntpath.normcase(os.path.realpath(c)) != ntpath.normcase(e.approved_target)
                or default_is_reparse(c)
            ):
                continue
            if self.isfile(c):
                return c
        return None

    def launch(self, e: AppEntry) -> str:
        exe = self.resolve_exe(e)
        if exe is None:
            raise AppError(f"{e.name} was not found on this PC")
        kwargs = dict(shell=False, close_fds=True, stdin=subprocess.DEVNULL,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if e.empty_console and self.is_windows:
            kwargs["creationflags"] = CREATE_NEW_CONSOLE   # opens an empty console window
        args = e.args
        if e.empty_console:
            args = {"cmd.exe": ("/D",), "powershell.exe": ("-NoLogo", "-NoProfile")}.get(e.exe.casefold(), ())
        self.popen([exe, *args], **kwargs)
        return exe

    def close_argv(self, e: AppEntry) -> list:
        if not e.closable:
            raise AppError(f"{e.name} is not closed automatically")
        root = self.env.get("SystemRoot") or "C:\\Windows"
        return [ntpath.join(root, "System32", "taskkill.exe"), "/IM", e.exe]   # no /F: graceful request
