"""SQLite store for tasks and reminders. Pure Python (sqlite3), no Qt.

Times are naive LOCAL datetimes stored as ISO strings 'YYYY-MM-DDTHH:MM:SS'.
Deletion is soft (status='deleted'). Alerts are claimed atomically via
`alerted_for`, which survives restarts, so a reminder fires once per due time.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

SCHEMA_VERSION = 1
FMT = "%Y-%m-%dT%H:%M:%S"
MAX_TITLE = 500
MAX_SNOOZE_MINUTES = 7 * 24 * 60
_EFF = "COALESCE(snoozed_until, due_at)"


class RepoError(Exception):
    pass


class NotFoundError(RepoError):
    pass


class StateError(RepoError):
    pass


class SchemaTooNewError(RepoError):
    pass


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.strftime(FMT) if dt else None


def parse_iso(s: Optional[str]) -> Optional[datetime]:
    return datetime.strptime(s, FMT) if s else None


@dataclass(frozen=True)
class Item:
    id: int
    type: str
    title: str
    due_at: Optional[datetime]
    status: str
    created_at: datetime
    snoozed_until: Optional[datetime]
    completed_at: Optional[datetime]
    alerted_for: Optional[str]

    @property
    def effective_due(self) -> Optional[datetime]:
        return self.snoozed_until or self.due_at


def _row(r: sqlite3.Row) -> Item:
    return Item(r["id"], r["type"], r["title"], parse_iso(r["due_at"]), r["status"],
                parse_iso(r["created_at"]), parse_iso(r["snoozed_until"]),
                parse_iso(r["completed_at"]), r["alerted_for"])


class Repository:
    def __init__(self, path: str = ":memory:"):
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=5)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    def close(self) -> None:
        """Safe if called more than once; no user data is deleted."""
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    @contextmanager
    def _tx(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            else:
                self._db.execute("COMMIT")

    def _migrate(self) -> None:
        with self._lock:
            v = self._db.execute("PRAGMA user_version").fetchone()[0]
            if v > SCHEMA_VERSION:
                raise SchemaTooNewError(f"database schema {v} is newer than this Jarvix ({SCHEMA_VERSION})")
            if v < 1:
                with self._tx() as db:
                    db.execute("""CREATE TABLE items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        type TEXT NOT NULL CHECK (type IN ('task','reminder')),
                        title TEXT NOT NULL,
                        due_at TEXT,
                        status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','done','deleted')),
                        created_at TEXT NOT NULL,
                        snoozed_until TEXT,
                        completed_at TEXT,
                        alerted_for TEXT)""")
                    db.execute("CREATE INDEX idx_items_due ON items(status, due_at)")
                    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # ------------------------------------------------------------ create/read
    def add(self, type_: str, title: str, due_at: Optional[datetime], now: datetime) -> Item:
        title = (title or "").strip()
        if type_ not in ("task", "reminder"):
            raise ValueError("type must be 'task' or 'reminder'")
        if not title:
            raise ValueError("title is required")
        if len(title) > MAX_TITLE:
            raise ValueError(f"title longer than {MAX_TITLE} characters")
        if type_ == "reminder" and due_at is None:
            raise ValueError("a reminder needs a due time")
        with self._tx() as db:
            cur = db.execute("INSERT INTO items(type,title,due_at,status,created_at) VALUES (?,?,?,?,?)",
                             (type_, title, iso(due_at), "open", iso(now)))
            return self._get(cur.lastrowid)

    def _get(self, id_: int) -> Item:
        r = self._db.execute("SELECT * FROM items WHERE id=?", (id_,)).fetchone()
        if r is None:
            raise NotFoundError(f"item {id_} not found")
        return _row(r)

    def get(self, id_: int) -> Item:
        with self._lock:
            return self._get(id_)

    def get_active(self, id_: int) -> Item:
        """Like get(), but deleted items count as not found."""
        it = self.get(id_)
        if it.status == "deleted":
            raise NotFoundError(f"item {id_} not found")
        return it

    def items_between(self, start: Optional[datetime], end: Optional[datetime],
                      statuses=("open",), *, type_=None, limit=None) -> list:
        sql = f"SELECT * FROM items WHERE status IN ({','.join('?' * len(statuses))})"
        args: list = list(statuses)
        if start is not None:
            sql += f" AND {_EFF} >= ?"
            args.append(iso(start))
        if end is not None:
            sql += f" AND {_EFF} < ?"
            args.append(iso(end))
        if type_ is not None:
            sql += " AND type = ?"
            args.append(type_)
        sql += f" AND {_EFF} IS NOT NULL ORDER BY {_EFF}, id"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        with self._lock:
            return [_row(r) for r in self._db.execute(sql, args)]

    def undated_open(self, limit: int = 20) -> list:
        with self._lock:
            return [_row(r) for r in self._db.execute(
                "SELECT * FROM items WHERE status='open' AND due_at IS NULL ORDER BY id LIMIT ?", (limit,))]

    def today_view(self, now: datetime) -> list:
        """Overdue open items plus everything due today."""
        tomorrow = datetime.combine(now.date() + timedelta(days=1), datetime.min.time())
        return self.items_between(None, tomorrow)

    def tomorrow_view(self, now: datetime) -> list:
        s = datetime.combine(now.date() + timedelta(days=1), datetime.min.time())
        return self.items_between(s, s + timedelta(days=1))

    def next7_view(self, now: datetime) -> list:
        s = datetime.combine(now.date(), datetime.min.time())
        return self.items_between(None, s + timedelta(days=7))

    def done_today(self, now: datetime) -> list:
        s = datetime.combine(now.date(), datetime.min.time())
        with self._lock:
            return [_row(r) for r in self._db.execute(
                "SELECT * FROM items WHERE status='done' AND completed_at >= ? ORDER BY completed_at",
                (iso(s),))]

    # ------------------------------------------------------------ change
    def _open_item(self, db, id_: int) -> Item:
        r = db.execute("SELECT * FROM items WHERE id=?", (id_,)).fetchone()
        if r is None or r["status"] == "deleted":
            raise NotFoundError(f"item {id_} not found")
        it = _row(r)
        if it.status != "open":
            raise StateError(f"item {id_} is already {it.status}")
        return it

    def complete(self, id_: int, now: datetime) -> Item:
        with self._tx() as db:
            self._open_item(db, id_)
            db.execute("UPDATE items SET status='done', completed_at=? WHERE id=?", (iso(now), id_))
            return self._get(id_)

    def snooze(self, id_: int, now: datetime, minutes: Optional[int] = None,
               until: Optional[datetime] = None) -> Item:
        if (minutes is None) == (until is None):
            raise ValueError("give either minutes or until")
        if minutes is not None:
            if not 1 <= minutes <= MAX_SNOOZE_MINUTES:
                raise ValueError("snooze must be between 1 minute and 7 days")
            until = now.replace(second=0, microsecond=0) + timedelta(minutes=minutes)
        if until <= now:
            raise ValueError("snooze time must be in the future")
        with self._tx() as db:
            self._open_item(db, id_)
            db.execute("UPDATE items SET snoozed_until=? WHERE id=?", (iso(until), id_))
            return self._get(id_)

    def reschedule(self, id_: int, due_at: datetime, now: datetime) -> Item:
        with self._tx() as db:
            self._open_item(db, id_)
            db.execute("UPDATE items SET due_at=?, snoozed_until=NULL WHERE id=?", (iso(due_at), id_))
            return self._get(id_)

    def soft_delete(self, id_: int) -> Item:
        with self._tx() as db:
            r = db.execute("SELECT status FROM items WHERE id=?", (id_,)).fetchone()
            if r is None or r["status"] == "deleted":
                raise NotFoundError(f"item {id_} not found")
            db.execute("UPDATE items SET status='deleted' WHERE id=?", (id_,))
            return self._get(id_)

    # ------------------------------------------------------------ alerts
    def pending_alerts(self, now: datetime) -> list:
        sql = (f"SELECT * FROM items WHERE type='reminder' AND status='open' AND {_EFF} <= ? "
               f"AND (alerted_for IS NULL OR alerted_for != {_EFF}) ORDER BY {_EFF}, id")
        with self._lock:
            return [_row(r) for r in self._db.execute(sql, (iso(now),))]

    def claim_alert(self, id_: int, effective: datetime) -> bool:
        """Atomically mark this due time as alerted. True for exactly one caller."""
        with self._tx() as db:
            cur = db.execute(
                f"UPDATE items SET alerted_for=? WHERE id=? AND status='open' AND {_EFF}=? "
                f"AND (alerted_for IS NULL OR alerted_for != ?)",
                (iso(effective), id_, iso(effective), iso(effective)))
            return cur.rowcount == 1

    def recently_alerted_open(self, now: datetime) -> list:
        sql = (f"SELECT * FROM items WHERE status='open' AND alerted_for = {_EFF} AND {_EFF} <= ? "
               f"ORDER BY {_EFF} DESC, id DESC")
        with self._lock:
            return [_row(r) for r in self._db.execute(sql, (iso(now),))]
