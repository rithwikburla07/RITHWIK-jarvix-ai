"""Multi-turn dialogue: ask ONE question per turn, keep filled slots, support cancel/expiry."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from ..tasks import datetime_parser as dtp
from .intents import Intent
from .parser import Parser, resolve, _title_clean

# slot -> (locale key, English fallback). Real strings move to data/lang/*.json at the i18n checkpoint.
QUESTIONS = {
    "title": ("ask.title", "What should I remind you about?"),
    "time": ("ask.time", "What time?"),
    "time_past": ("ask.time_past", "That time has already passed. What time should I use?"),
    "meridiem": ("ask.meridiem", "AM or PM?"),
    "contact": ("ask.contact", "Who should I message?"),
    "contact_choice": ("ask.contact_choice", "Which one: {options}?"),
    "app_choice": ("ask.app_choice", "Which app: {options}?"),
    "message": ("ask.message", "What should the message say?"),
    "duration": ("ask.duration", "For how long? For example, 10 minutes."),
    "item_id": ("ask.item_id", "Which number?"),
}


@dataclass
class DialogResult:
    kind: str                      # intent | ask | cancelled | nothing_to_cancel | expired
    intent: Optional[Intent] = None
    question_key: str = ""
    question: str = ""


def _ask(slot: str, **fmt) -> tuple:
    key, text = QUESTIONS[slot]
    return key, text.format(**fmt) if fmt else text


def missing_slot(intent: Intent) -> Optional[str]:
    n, s = intent.name, intent.slots
    if n in ("create_reminder", "create_task", "reschedule"):
        if n != "reschedule" and not s.get("title"):
            return "title"
        if s.get("ambiguous_hour"):
            return "meridiem"
        if n == "create_task":
            return None
        if s.get("relative_dt") is None and s.get("time") is None:
            return "time"
    elif n in ("whatsapp_message", "whatsapp_call"):
        if s.get("candidates"):
            return "contact_choice"
        if not s.get("contact"):
            return "contact"
        if n == "whatsapp_message" and not s.get("message"):
            return "message"
    elif n == "snooze":
        if s.get("minutes") is None and s.get("time") is None and not s.get("ambiguous_hour"):
            return "duration"
        if s.get("ambiguous_hour"):
            return "meridiem"
    elif n == "open_app" and not s.get("app"):
        return "app_choice"
    return None


class DialogManager:
    def __init__(self, parser: Parser, ttl_seconds: int = 120, max_attempts: int = 3):
        self.parser, self.ttl, self.max_attempts = parser, ttl_seconds, max_attempts
        self.pending: Optional[Intent] = None
        self._slot: Optional[str] = None
        self._since: Optional[datetime] = None
        self._attempts = 0

    # ------------------------------------------------------------ public
    def clear(self) -> None:
        self.pending = self._slot = self._since = None
        self._attempts = 0

    def handle(self, text: str, now: Optional[datetime] = None) -> DialogResult:
        now = now or datetime.now()
        plain = (text or "").strip()
        if self.pending and self._since and now - self._since > timedelta(seconds=self.ttl):
            self.clear()

        if self.pending:
            if re.fullmatch(r"(?:cancel|never\s*mind|forget\s+it|stop|abort)[.!]?", plain, re.I):
                self.clear()
                return DialogResult("cancelled")
            return self._continue(plain, now)

        intent = self.parser.parse(text, now)
        if intent.name == "cancel":
            return DialogResult("nothing_to_cancel")
        return self._advance(intent, now, fresh=True)

    # ------------------------------------------------------------ internals
    def _advance(self, intent: Intent, now: datetime, fresh: bool = False) -> DialogResult:
        slot = missing_slot(intent)
        if slot is None:
            slot = self._finalize(intent, now)
        if slot is None:
            self.clear()
            return DialogResult("intent", intent)
        if fresh:
            self._attempts = 0
        self.pending, self._slot, self._since = intent, slot, now
        opts = ", ".join(intent.slots.get("candidates") or [])
        key, q = _ask(slot, options=opts) if "{options}" in QUESTIONS[slot][1] else _ask(slot)
        return DialogResult("ask", intent, key, q)

    def _finalize(self, intent: Intent, now: datetime) -> Optional[str]:
        s = intent.slots
        if intent.name in ("create_reminder", "reschedule") or (
                intent.name == "create_task" and (s.get("time") or s.get("relative_dt"))):
            due = dtp.resolve_due(s.get("date"), s.get("time"), s.get("relative_dt"), now)
            if due is not None and due <= now:
                s.update(date=None, time=None, relative_dt=None)
                return "time_past"
            s["due_at"] = due
        if intent.name == "snooze" and s.get("minutes") is None and s.get("time"):
            due = dtp.resolve_due(s.get("date"), s.get("time"), None, now)
            s["until"] = due
        return None

    def _continue(self, text: str, now: datetime) -> DialogResult:
        intent, slot = self.pending, self._slot
        outcome = self._fill(intent, slot, text, now)
        if outcome == "nomatch":
            # A complete, unrelated command replaces the pending one.
            other = self.parser.parse(text, now)
            if other.name not in ("unknown", "cancel") and slot not in ("title", "message"):
                self.clear()
                return self._advance(other, now, fresh=True)
            self._attempts += 1
            if self._attempts >= self.max_attempts:
                self.clear()
                return DialogResult("cancelled")
            key, q = _ask(slot, options=", ".join(intent.slots.get("candidates") or []))
            return DialogResult("ask", intent, key, q)
        self._attempts = 0
        return self._advance(intent, now)

    def _fill(self, intent: Intent, slot: str, text: str, now: datetime) -> str:
        s = intent.slots
        if slot in ("title",):
            pw = dtp.parse_when(text, now)
            title = _title_clean(pw.remainder if pw.found else text)
            if not title:
                return "nomatch"
            s["title"] = title
            self._merge_when(s, pw)
            return "filled"
        if slot in ("time", "time_past"):
            pw = dtp.parse_when(self.parser.locale.time_text(text), now)
            if not pw.found:
                return "nomatch"
            self._merge_when(s, pw)
            return "filled"
        if slot == "meridiem":
            text = self.parser.locale.time_text(text)
            t = dtp.apply_meridiem(s["ambiguous_hour"], text)
            if t is None:
                pw = dtp.parse_when(text, now)
                if pw.time is None:
                    return "nomatch"
                t = pw.time
            s["time"], s["ambiguous_hour"] = t, None
            return "filled"
        if slot in ("contact", "contact_choice"):
            found = resolve(text, self.parser.cat.contact_idx)
            if not found:
                return "nomatch"
            s["contact"] = found[0] if len(found) == 1 else None
            s["candidates"] = found if len(found) > 1 else []
            return "filled"
        if slot == "app_choice":
            found = resolve(text, self.parser.cat.app_idx)
            if len(found) != 1:
                return "nomatch"
            s["app"] = found[0]
            return "filled"
        if slot == "message":
            m = text.strip()
            if not m:
                return "nomatch"
            s["message"] = m  # exactly as typed
            return "filled"
        if slot == "duration":
            text = self.parser.locale.time_text(text)
            mins = dtp.parse_duration(text)
            if mins is not None:
                s["minutes"] = mins
                return "filled"
            pw = dtp.parse_when(text, now)
            if not pw.found:
                return "nomatch"
            s.update(time=pw.time, date=pw.date, ambiguous_hour=pw.ambiguous_hour)
            return "filled"
        return "nomatch"

    @staticmethod
    def _merge_when(s: dict, pw) -> None:
        if pw.relative_dt is not None:
            s["relative_dt"] = pw.relative_dt
        if pw.date is not None:
            s["date"] = pw.date
        if pw.time is not None:
            s["time"] = pw.time
        if pw.ambiguous_hour is not None:
            s["ambiguous_hour"] = pw.ambiguous_hour
