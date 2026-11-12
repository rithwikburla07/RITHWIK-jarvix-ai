"""Minimal, safe iCalendar (.ics) export/import for tasks and reminders. Pure Python.

Export writes floating LOCAL times (no Z) so any calendar shows the same clock time.
Import limits: 1 MB input, 1000 events; recurring rules are ignored (imported once).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Optional

MAX_BYTES = 1_000_000
MAX_EVENTS = 1000
MAX_TITLE = 500


@dataclass(frozen=True)
class IcsEvent:
    title: str
    due: datetime
    is_reminder: bool


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def _unesc(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            out.append("\n" if n in "nN" else n)
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _fold(line: str) -> str:
    out, cur, size = [], "", 0
    for ch in line:
        b = len(ch.encode("utf-8"))
        if size + b > 75:
            out.append(cur)
            cur, size = " " + ch, 1 + b
        else:
            cur += ch
            size += b
    out.append(cur)
    return "\r\n".join(out)


def export_ics(items, now: datetime) -> str:
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Jarvix//Personal AI 1.1.7//EN", "CALSCALE:GREGORIAN"]
    for it in items:
        due = it.due_at
        if due is None or it.status == "deleted":
            continue
        lines += ["BEGIN:VEVENT", f"UID:jarvix-{it.id}@jarvix.local", f"DTSTAMP:{stamp}",
                  f"DTSTART:{due.strftime('%Y%m%dT%H%M%S')}", f"SUMMARY:{_esc(it.title)}",
                  f"X-JARVIX-TYPE:{it.type}", f"STATUS:{'COMPLETED' if it.status == 'done' else 'CONFIRMED'}"]
        if it.type == "reminder":
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_esc(it.title)}", "TRIGGER:PT0S", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


def _unfold(text: str) -> list:
    out: list = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and out:
            out[-1] += raw[1:]
        else:
            out.append(raw)
    return out


def _parse_dt(value: str, params: dict) -> tuple:
    """-> (datetime, warning|None). Raises ValueError if unparseable."""
    v = value.strip()
    if params.get("VALUE", "").upper() == "DATE" or (len(v) == 8 and v.isdigit()):
        d = datetime.strptime(v, "%Y%m%d")
        return datetime.combine(d.date(), time(9, 0)), "all-day event imported at 09:00"
    if v.endswith("Z"):
        dt = datetime.strptime(v[:-1], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        return dt.astimezone().replace(tzinfo=None), None
    dt = datetime.strptime(v, "%Y%m%dT%H%M%S")
    tz = params.get("TZID")
    return dt, (f"time zone '{tz}' not converted; treated as local time" if tz else None)


def parse_ics(text: str) -> tuple:
    """-> (events, warnings). Never raises on malformed events; skips them with a warning."""
    if len(text.encode("utf-8", "ignore")) > MAX_BYTES:
        raise ValueError("calendar file is larger than 1 MB")
    events, warnings = [], []
    cur: Optional[dict] = None
    depth_alarm = 0
    for line in _unfold(text):
        up = line.strip().upper()
        if up == "BEGIN:VEVENT":
            cur, depth_alarm = {"alarm": False, "warn": []}, 0
            continue
        if cur is None:
            continue
        if up == "BEGIN:VALARM":
            depth_alarm += 1
            cur["alarm"] = True
            continue
        if up == "END:VALARM":
            depth_alarm = max(0, depth_alarm - 1)
            continue
        if up == "END:VEVENT":
            n = len(events) + 1
            if len(events) >= MAX_EVENTS:
                warnings.append(f"stopped after {MAX_EVENTS} events")
                break
            title, dtv = cur.get("summary", "").strip(), cur.get("dtstart")
            if not title:
                warnings.append(f"event {n}: no title, skipped")
            elif dtv is None:
                warnings.append(f"event '{title[:40]}': no usable start time, skipped")
            else:
                warnings += [f"event '{title[:40]}': {w}" for w in cur["warn"]]
                events.append(IcsEvent(title[:MAX_TITLE], dtv, cur["alarm"] or cur.get("type") == "reminder"))
            cur = None
            continue
        if depth_alarm or ":" not in line:
            continue
        head, _, value = line.partition(":")
        name, *plist = head.split(";")
        params = dict(p.split("=", 1) for p in plist if "=" in p)
        name = name.strip().upper()
        if name == "SUMMARY":
            cur["summary"] = _unesc(value)
        elif name == "DTSTART":
            try:
                cur["dtstart"], w = _parse_dt(value, {k.upper(): v for k, v in params.items()})
                if w:
                    cur["warn"].append(w)
            except ValueError:
                cur["dtstart"] = None
        elif name == "X-JARVIX-TYPE":
            cur["type"] = value.strip().lower()
        elif name == "RRULE":
            cur["warn"].append("recurring event imported once (repeats ignored)")
    return events, warnings
