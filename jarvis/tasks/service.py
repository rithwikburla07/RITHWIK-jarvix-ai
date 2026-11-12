"""Task/reminder behaviour behind the command engine. Pure Python, English text with locale keys."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional

from ..core.intents import Intent, Reply
from . import ics
from .repository import Item, NotFoundError, Repository, StateError


def _clock(dt: datetime) -> str:
    return f"{dt.hour % 12 or 12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def fmt_due(dt: Optional[datetime], now: datetime, any_time: bool = False) -> str:
    if dt is None:
        return "no due date"
    t = "" if any_time else f" at {_clock(dt)}"
    d = dt.date()
    if d == now.date():
        return f"today{t}"
    if d == now.date() + timedelta(days=1):
        return f"tomorrow{t}"
    if now.date() < d < now.date() + timedelta(days=7):
        return f"{dt.strftime('%A')}{t}"
    year = f" {dt.year}" if dt.year != now.year else ""
    return f"{dt.strftime('%a')} {dt.day} {dt.strftime('%b')}{year}{t}"


def _any_time(it: Item) -> bool:
    return it.type == "task" and it.due_at is not None and it.due_at.time() == time(23, 59)


def describe(it: Item, now: datetime) -> str:
    eff = it.effective_due
    when = fmt_due(eff, now, _any_time(it))
    flag = " (snoozed)" if it.snoozed_until else ""
    over = " (overdue)" if it.status == "open" and eff and eff < now and not _any_time(it) else ""
    return f"#{it.id} {it.type} - {it.title} - {when}{flag}{over}"


class TaskService:
    def __init__(self, repo: Repository):
        self.repo = repo

    # ----------------------------------------------------------- create
    def create(self, intent: Intent, now: datetime) -> Reply:
        s, kind = intent.slots, intent.slots.get("kind", "reminder")
        due = s.get("due_at")
        if due is None and kind == "task" and s.get("date"):
            due = datetime.combine(s["date"], time(23, 59))
        try:
            it = self.repo.add(kind, s.get("title", ""), due, now)
        except ValueError as exc:
            return Reply("error", f"I couldn't save that: {exc}.", "task.error", intent=intent)
        label = "Reminder" if kind == "reminder" else "Task"
        when = f", {fmt_due(it.due_at, now, _any_time(it))}" if it.due_at else ""
        return Reply("done", f"{label} #{it.id} saved: {it.title}{when}.", "task.saved", {"id": it.id}, intent)

    # ----------------------------------------------------------- views
    def show(self, rng: str, now: datetime) -> Reply:
        if rng == "tomorrow":
            items, title = self.repo.tomorrow_view(now), "Tomorrow"
        elif rng == "week":
            items, title = self.repo.next7_view(now), "Next 7 days"
        else:
            items, title = self.repo.today_view(now), "Today"
        lines = [describe(i, now) for i in items]
        extra = self.repo.undated_open() if rng == "today" else []
        if extra:
            lines += [describe(i, now) for i in extra]
        if not lines:
            return Reply("info", f"{title}: nothing scheduled.", "task.none", {"items": []})
        return Reply("info", f"{title}:\n" + "\n".join(lines), "task.list", {"items": items + extra})

    def calendar(self, day, now):
        if type(day) is not date:
            return Reply("error", "Choose a calendar date.", "task.calendar")
        start = datetime.combine(day, time.min)
        items = self.repo.items_between(start, start + timedelta(days=1))
        text = "\n".join(describe(item, now) for item in items) or "Nothing scheduled for that day."
        return Reply("info", text, "task.calendar", {"items": items})

    # ----------------------------------------------------------- change
    def _fail(self, exc: Exception, id_: int) -> Reply:
        if isinstance(exc, NotFoundError):
            return Reply("error", f"I can't find item #{id_}.", "task.notfound")
        if isinstance(exc, StateError):
            return Reply("info", f"Item #{id_} is {str(exc).split(' is ')[-1]}.", "task.state")
        return Reply("error", f"I couldn't do that: {exc}.", "task.error")

    def complete(self, id_: int, now: datetime) -> Reply:
        try:
            it = self.repo.complete(id_, now)
        except (NotFoundError, StateError) as exc:
            return self._fail(exc, id_)
        return Reply("done", f"Marked #{it.id} done: {it.title}.", "task.done", {"id": id_})

    def snooze_candidates(self, now: datetime) -> list:
        return self.repo.recently_alerted_open(now)

    def snooze(self, id_: int, now: datetime, minutes: Optional[int], until: Optional[datetime]) -> Reply:
        try:
            it = self.repo.snooze(id_, now, minutes=minutes, until=None if minutes else until)
        except (NotFoundError, StateError, ValueError) as exc:
            return self._fail(exc, id_)
        return Reply("done", f"Snoozed #{it.id} ({it.title}) until {fmt_due(it.snoozed_until, now)}.", "task.snoozed", {"id": id_})

    def reschedule(self, id_: int, due: datetime, now: datetime) -> Reply:
        try:
            it = self.repo.reschedule(id_, due, now)
        except (NotFoundError, StateError) as exc:
            return self._fail(exc, id_)
        return Reply("done", f"Moved #{it.id} ({it.title}) to {fmt_due(it.due_at, now)}.", "task.moved", {"id": id_})

    def delete_preview(self, id_: int, kind: Optional[str]) -> tuple:
        """-> (item, None) or (None, error Reply). Nothing is changed."""
        try:
            it = self.repo.get_active(id_)
        except NotFoundError:
            return None, Reply("error", f"I can't find item #{id_}.", "task.notfound")
        if kind and it.type != kind:
            return None, Reply("error", f"Item #{id_} is a {it.type}, not a {kind}. Nothing was deleted.", "task.kind_mismatch")
        return it, None

    def delete(self, id_: int, now: datetime) -> Reply:
        try:
            it = self.repo.soft_delete(id_)
        except NotFoundError as exc:
            return self._fail(exc, id_)
        return Reply("done", f"Deleted #{it.id}: {it.title}.", "task.deleted", {"id": id_})

    # ----------------------------------------------------------- calendar files
    def export_ics(self, now: datetime) -> str:
        return ics.export_ics(self.repo.items_between(None, None, ("open", "done")), now)

    def import_ics(self, text: str, now: datetime) -> Reply:
        try:
            events, warnings = ics.parse_ics(text)
        except ValueError as exc:
            return Reply("error", f"Can't import that calendar: {exc}.", "ics.error")
        existing = {(i.title, i.due_at) for i in self.repo.items_between(None, None, ("open", "done"))}
        added = skipped = 0
        for ev in events:
            if (ev.title, ev.due) in existing:
                skipped += 1
                continue
            self.repo.add("reminder" if ev.is_reminder else "task", ev.title, ev.due, now)
            existing.add((ev.title, ev.due))
            added += 1
        text = f"Imported {added} event(s); {skipped} duplicate(s) skipped."
        if warnings:
            text += "\nNotes:\n" + "\n".join(f"  - {w}" for w in warnings[:10])
        return Reply("done", text, "ics.imported", {"added": added, "skipped": skipped, "warnings": warnings})
