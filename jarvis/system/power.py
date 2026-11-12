"""Power plans via powercfg. Argument lists only; GUIDs are validated before use.

Changing a plan is a state change: the engine asks for a typed confirmation first
(see actions.py). Listing plans is read-only.
"""
from __future__ import annotations

import ntpath
import re
from dataclasses import dataclass

_GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_LINE = re.compile(rf"Power Scheme GUID:\s*({_GUID})\s*\((.*?)\)\s*(\*)?")
_GUID_RE = re.compile(rf"^{_GUID}$")


class PowerError(Exception):
    pass


@dataclass(frozen=True)
class Plan:
    guid: str
    name: str
    active: bool


def parse_list(text: str) -> list:
    plans = []
    for line in (text or "").splitlines():
        m = _LINE.search(line)
        if m:
            plans.append(Plan(m.group(1).lower(), m.group(2).strip(), bool(m.group(3))))
    return plans


class PowerController:
    def __init__(self, capture, system_root: str = "C:\\Windows"):
        self.capture = capture            # capture(argv) -> (returncode, stdout)
        self.root = system_root

    def _exe(self, name: str) -> str:
        return ntpath.join(self.root, "System32", name)

    def list(self) -> list:
        rc, out = self.capture([self._exe("powercfg.exe"), "/list"])
        if rc != 0:
            raise PowerError("Windows didn't list the power plans.")
        plans = parse_list(out)
        if not plans:
            raise PowerError("No power plans were found.")
        return plans

    def set_argv(self, guid: str) -> list:
        if not _GUID_RE.match(guid or ""):
            raise ValueError("invalid power plan id")
        return [self._exe("powercfg.exe"), "/setactive", guid.lower()]

    def settings_argv(self) -> list:
        return [ntpath.join(self.root, "explorer.exe"), "ms-settings:batterysaver"]
