"""Natural-language date/time parsing in LOCAL time (naive datetimes).

All functions take `now` explicitly so results are deterministic in tests.
No time-zone conversion is applied: the machine's local clock is the
reference and results are naive local datetimes.

"next <weekday>" means the upcoming occurrence strictly after today.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tues": 1, "tue": 1,
    "wednesday": 2, "wed": 2, "thursday": 3, "thurs": 3, "thur": 3, "thu": 3,
    "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sept": 9, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
_WD = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
_I = re.I

REL = re.compile(r"\b(?:in|after)\s+(\d+|an?)\s*(minutes?|mins?|hours?|hrs?|days?)\b(?:\s+from\s+now)?", _I)
MONTH_DAY = re.compile(
    rf"\b(?:on\s+)?(?:the\s+)?(?P<mon>{_MON})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(?P<year>\d{{4}}))?", _I)
DAY_MONTH = re.compile(
    rf"\b(?:on\s+)?(?:the\s+)?(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<mon>{_MON})\b\.?(?:,?\s+(?P<year>\d{{4}}))?", _I)
REL_DAY = re.compile(r"\b(?P<w>day\s+after\s+tomorrow|tomorrow|today|tonight)\b", _I)
WEEKDAY = re.compile(rf"\b(?:(?P<pre>on|next|this|coming)\s+)?(?P<wd>{_WD})\b", _I)
TIME12 = re.compile(r"(?:\bat\s+|@\s*)?\b(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)(?!\w)", _I)
TIME_COLON = re.compile(r"(?:\bat\s+)?\b(?P<h>\d{1,2}):(?P<m>\d{2})\b", _I)
NOON = re.compile(r"(?:\bat\s+)?\b(?P<w>noon|midnight)\b", _I)
BARE_AT = re.compile(r"\bat\s+(?P<h>\d{1,2})\b(?!\s*[:%])", _I)
DURATION = re.compile(r"\b(?P<n>\d+|an?|half\s+an?)\s*(?P<u>minutes?|mins?|hours?|hrs?)\b", _I)


@dataclass
class ParsedWhen:
    date: Optional[date] = None
    time: Optional[time] = None
    relative_dt: Optional[datetime] = None
    ambiguous_hour: Optional[tuple] = None  # (hour, minute) when AM/PM unknown
    invalid: bool = False
    remainder: str = ""

    @property
    def found(self) -> bool:
        return bool(self.date or self.time or self.relative_dt or self.ambiguous_hour)


def _blank(work: str, m: "re.Match") -> str:
    return work[:m.start()] + " " * (m.end() - m.start()) + work[m.end():]


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip(" ,.;:-")


def _meridiem(h: int, ap: str) -> int:
    return (h % 12) + (12 if ap.lower().startswith("p") else 0)


def parse_when(text: str, now: datetime) -> ParsedWhen:
    pw = ParsedWhen()
    work = text or ""
    today = now.date()

    m = REL.search(work)
    if m:
        n = 1 if m.group(1).lower() in ("a", "an") else int(m.group(1))
        unit = m.group(2).lower()
        base = now.replace(second=0, microsecond=0)
        if unit.startswith("min"):
            pw.relative_dt = base + timedelta(minutes=n)
        elif unit.startswith("h"):
            pw.relative_dt = base + timedelta(hours=n)
        else:
            pw.date = today + timedelta(days=n)
        work = _blank(work, m)

    for rx in (MONTH_DAY, DAY_MONTH):
        m = rx.search(work)
        if m and pw.date is None:
            mon, day = MONTHS[m.group("mon").lower()], int(m.group("day"))
            explicit = m.group("year")
            year = int(explicit) if explicit else today.year
            try:
                d = date(year, mon, day)
                if not explicit and d < today:
                    d = date(year + 1, mon, day)
                pw.date = d
            except ValueError:
                pw.invalid = True
            work = _blank(work, m)
            break

    if pw.date is None:
        m = REL_DAY.search(work)
        if m:
            w = m.group("w").lower()
            off = 2 if w.startswith("day after") else 1 if w == "tomorrow" else 0
            pw.date = today + timedelta(days=off)
            work = _blank(work, m)
    if pw.date is None:
        m = WEEKDAY.search(work)
        if m:
            wd = WEEKDAYS[m.group("wd").lower()]
            delta = (wd - today.weekday()) % 7
            if (m.group("pre") or "").lower() == "next" and delta == 0:
                delta = 7
            pw.date = today + timedelta(days=delta)
            work = _blank(work, m)

    m = TIME12.search(work)
    if m:
        h, mi = int(m.group("h")), int(m.group("m") or 0)
        if 1 <= h <= 12 and mi < 60:
            pw.time = time(_meridiem(h, m.group("ap")), mi)
        else:
            pw.invalid = True
        work = _blank(work, m)
    else:
        m = NOON.search(work)
        if m:
            pw.time = time(12 if m.group("w").lower() == "noon" else 0, 0)
            work = _blank(work, m)
        else:
            m = TIME_COLON.search(work)
            if m:
                raw_h, h, mi = m.group("h"), int(m.group("h")), int(m.group("m"))
                if h > 23 or mi > 59:
                    pw.invalid = True
                elif h >= 13 or h == 0 or raw_h.startswith("0"):
                    pw.time = time(h, mi)
                else:
                    pw.ambiguous_hour = (h, mi)
                work = _blank(work, m)
            else:
                m = BARE_AT.search(work)
                if m:
                    h = int(m.group("h"))
                    if 1 <= h <= 12:
                        pw.ambiguous_hour = (h, 0)
                    elif 13 <= h <= 23:
                        pw.time = time(h, 0)
                    else:
                        pw.invalid = True
                    work = _blank(work, m)

    pw.remainder = _clean(work)
    return pw


def apply_meridiem(hour_minute: tuple, answer: str) -> Optional[time]:
    a = answer.strip().lower().replace(".", "")
    if a in ("am", "pm"):
        return time(_meridiem(hour_minute[0], a), hour_minute[1])
    return None


def resolve_due(date_: Optional[date], time_: Optional[time],
                relative_dt: Optional[datetime], now: datetime) -> Optional[datetime]:
    """Combine parsed parts into one local datetime, or None if time is missing."""
    if relative_dt is not None:
        return relative_dt
    if time_ is None:
        return None
    if date_ is None:
        cand = datetime.combine(now.date(), time_)
        return cand if cand > now else cand + timedelta(days=1)
    return datetime.combine(date_, time_)


def parse_duration(text: str) -> Optional[int]:
    """Return minutes for '10 minutes', 'an hour', '2 hrs', or None."""
    m = DURATION.search(text or "")
    if not m:
        return None
    n = m.group("n").lower()
    qty = 0.5 if n.startswith("half") else 1 if n in ("a", "an") else int(n)
    minutes = qty * (60 if m.group("u").lower().startswith("h") else 1)
    return int(minutes) if minutes >= 1 else None
