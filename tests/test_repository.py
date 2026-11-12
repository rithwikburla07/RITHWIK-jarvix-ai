import os
import sqlite3
from datetime import datetime, timedelta

from jarvis.tasks.repository import (NotFoundError, Repository, SchemaTooNewError, StateError)
from jarvis.tasks.locking import SingleInstanceLock
from tests.helpers import NOW, raises


def mk():
    return Repository(":memory:")


def test_create_and_fields():
    r = mk()
    it = r.add("reminder", "  call Mom ", NOW + timedelta(hours=9), NOW)
    assert (it.id, it.type, it.title, it.status) == (1, "reminder", "call Mom", "open")
    assert it.due_at == datetime(2026, 11, 12, 19, 0) and it.created_at == NOW
    assert it.snoozed_until is None and it.completed_at is None and it.due_at.tzinfo is None


def test_validation():
    r = mk()
    assert raises(ValueError, r.add, "reminder", "x", None, NOW)       # reminder needs a time
    assert raises(ValueError, r.add, "task", "   ", None, NOW)
    assert raises(ValueError, r.add, "note", "x", None, NOW)
    assert raises(ValueError, r.add, "task", "x" * 501, None, NOW)
    assert r.add("task", "no due date", None, NOW).due_at is None


def test_sql_injection_text_is_stored_literally():
    r = mk()
    evil = "x'); DROP TABLE items;--"
    r.add("task", evil, None, NOW)
    assert r.get(1).title == evil and len(r.undated_open()) == 1


def test_complete_snooze_reschedule_delete():
    r = mk()
    a = r.add("reminder", "a", NOW + timedelta(hours=1), NOW)
    done = r.complete(a.id, NOW)
    assert done.status == "done" and done.completed_at == NOW
    assert raises(StateError, r.complete, a.id, NOW)
    b = r.add("reminder", "b", NOW + timedelta(hours=1), NOW)
    s = r.snooze(b.id, NOW, minutes=10)
    assert s.snoozed_until == datetime(2026, 11, 12, 10, 10) and s.effective_due == s.snoozed_until
    c = r.reschedule(b.id, NOW + timedelta(days=1), NOW)
    assert c.snoozed_until is None and c.due_at == datetime(2026, 11, 13, 10, 0)
    d = r.soft_delete(b.id)
    assert d.status == "deleted"
    assert raises(NotFoundError, r.get_active, b.id) and raises(NotFoundError, r.soft_delete, b.id)
    assert raises(NotFoundError, r.complete, b.id, NOW)


def test_snooze_bounds():
    r = mk()
    a = r.add("reminder", "a", NOW + timedelta(hours=1), NOW)
    assert raises(ValueError, r.snooze, a.id, NOW, minutes=0)
    assert raises(ValueError, r.snooze, a.id, NOW, minutes=10**6)
    assert raises(ValueError, r.snooze, a.id, NOW, until=NOW - timedelta(minutes=1))
    assert raises(ValueError, r.snooze, a.id, NOW)


def test_soft_deleted_items_vanish_from_views_and_alerts():
    r = mk()
    a = r.add("reminder", "gone", NOW - timedelta(minutes=5), NOW - timedelta(hours=1))
    r.soft_delete(a.id)
    assert r.today_view(NOW) == [] and r.pending_alerts(NOW) == []
    assert r.get(a.id).status == "deleted"       # row is kept (soft delete)


def test_views_today_tomorrow_week_boundaries():
    r = mk()
    over = r.add("reminder", "overdue", datetime(2026, 11, 10, 9, 0), NOW)
    t1 = r.add("task", "today late", datetime(2026, 11, 12, 23, 59), NOW)
    t2 = r.add("reminder", "tomorrow early", datetime(2026, 11, 13, 0, 0), NOW)
    t3 = r.add("reminder", "day 7", datetime(2026, 11, 18, 23, 0), NOW)
    t4 = r.add("reminder", "day 8", datetime(2026, 11, 19, 0, 0), NOW)
    ids = lambda v: [i.id for i in v]
    assert ids(r.today_view(NOW)) == [over.id, t1.id]
    assert ids(r.tomorrow_view(NOW)) == [t2.id]
    assert ids(r.next7_view(NOW)) == [over.id, t1.id, t2.id, t3.id]
    assert t4.id not in ids(r.next7_view(NOW))


def test_persistence_across_reopen(tmp_path):
    db = tmp_path / "j.db"
    r = Repository(db)
    r.add("reminder", "persist me", NOW + timedelta(hours=1), NOW)
    r.close()
    r2 = Repository(db)
    assert r2.get(1).title == "persist me"
    assert r2.add("task", "next", None, NOW).id == 2
    r2.close()


def test_migration_idempotent_and_future_schema_refused(tmp_path):
    db = tmp_path / "m.db"
    Repository(db).close()
    Repository(db).close()                      # re-open does not fail or re-create
    con = sqlite3.connect(db)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 1
    con.execute("PRAGMA user_version = 99")
    con.commit(); con.close()
    assert raises(SchemaTooNewError, Repository, db)


def test_transaction_rolls_back_on_error():
    r = mk()
    try:
        with r._tx() as db:
            db.execute("INSERT INTO items(type,title,status,created_at) VALUES ('task','half','open','x')")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert r.undated_open() == []


def test_single_instance_lock(tmp_path):
    p = tmp_path / "jarvix.lock"
    a, b = SingleInstanceLock(p), SingleInstanceLock(p)
    assert a.acquire() is True
    assert b.acquire() is False                 # a second scheduler for the same store is refused
    a.release()
    assert b.acquire() is True
    b.release()
