"""Typed command results shared by the parser, dialogue and (later) executor."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Intent:
    name: str
    slots: dict = field(default_factory=dict)
    raw: str = ""

    def get(self, key: str, default: Any = None) -> Any:
        return self.slots.get(key, default)


@dataclass
class Reply:
    """What the engine returns to any front end (console now, GUI/voice later)."""
    kind: str            # info | ask | confirm | done | error | refused | unknown | unavailable | exit
    text: str
    key: str = ""        # locale key (strings move to data/lang/*.json at the i18n checkpoint)
    data: dict = field(default_factory=dict)
    intent: "Intent | None" = None
