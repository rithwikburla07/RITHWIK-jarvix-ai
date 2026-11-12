import threading
import time as _time
from datetime import datetime, timedelta

from jarvis.tasks.repository import Repository
from jarvis.tasks.scheduler import Alert, Scheduler
from tests.helpers import NOW


def setup(db=":memory:"):
    repo = Repository(db)
    got = []
    return repo, got, Scheduler(repo, got.append, interval=0.02)


def test_alert_fires_once_not_before_due():
    repo, got, s = setup()
    repo.add("reminder", "call Mom", NOW + timedelta(hours=9), NOW)
    assert s.tick(NOW) == [] and got == []
    due = NOW + timedelta(hours=9)
    assert len(s.tick(due)) == 1
    assert s.tick(due) == [] and s.tick(due + timedelta(minutes=5)) == []   # no duplicates
    assert [a.item.title for a in got] == ["call Mom"] and got[0].missed is False


def test_no_duplicate_alert_after_restart(tmp_path):
    db = tmp_path / "r.db"
    repo, got, s = setup(db)
    repo.add("reminder", "x", NOW + timedelta(minutes=1), NOW)
    assert len(s.tick(NOW + timedelta(minutes=1))) == 1
    repo.close()
    repo2, got2, s2 = setup(db)                  # "restart"
    assert s2.tick(NOW + timedelta(minutes=2)) == [] and got2 == []
    repo2.close()


def test_missed_reminders_surface_after_downtime(tmp_path):
    db = tmp_path / "r.db"
    repo, _, _ = setup(db)
    repo.add("reminder", "while you were away", NOW + timedelta(minutes=5), NOW)
    repo.add("reminder", "just due", NOW + timedelta(hours=3), NOW)
    repo.close()
    repo2, got, s = setup(db)
    later = NOW + timedelta(hours=3, seconds=30)
    s.tick(later)
    by = {a.item.title: a.missed for a in got}
    assert by == {"while you were away": True, "just due": False}


def test_snooze_alerts_again_exactly_once():
    repo, got, s = setup()
    r = repo.add("reminder", "stretch", NOW, NOW)
    s.tick(NOW)
    repo.snooze(r.id, NOW, minutes=10)
    assert s.tick(NOW + timedelta(minutes=5)) == []
    assert len(s.tick(NOW + timedelta(minutes=10))) == 1
    assert s.tick(NOW + timedelta(minutes=11)) == []
    assert len(got) == 2


def test_completed_or_deleted_reminders_never_alert():
    repo, got, s = setup()
    a = repo.add("reminder", "a", NOW, NOW - timedelta(hours=1))
    b = repo.add("reminder", "b", NOW, NOW - timedelta(hours=1))
    repo.complete(a.id, NOW); repo.soft_delete(b.id)
    assert s.tick(NOW + timedelta(minutes=1)) == []


def test_tasks_do_not_alert():
    repo, got, s = setup()
    repo.add("task", "t", NOW - timedelta(hours=1), NOW - timedelta(hours=2))
    assert s.tick(NOW) == []


def test_claim_is_atomic_across_two_schedulers(tmp_path):
    db = tmp_path / "r.db"
    repo_a, repo_b = Repository(db), Repository(db)
    repo_a.add("reminder", "once", NOW, NOW)
    got = []
    sa, sb = Scheduler(repo_a, got.append), Scheduler(repo_b, got.append)
    sa.tick(NOW); sb.tick(NOW)
    assert len(got) == 1


def test_handler_exception_does_not_break_scheduler_or_duplicate():
    repo = Repository()
    repo.add("reminder", "x", NOW, NOW)
    def bad(alert):
        raise RuntimeError("toast failed")
    s = Scheduler(repo, bad)
    assert len(s.tick(NOW)) == 1
    assert s.tick(NOW) == []


def test_background_thread_fires_and_stops_cleanly():
    repo = Repository()
    now = datetime.now()
    repo.add("reminder", "live", now - timedelta(seconds=1), now - timedelta(minutes=1))
    seen = threading.Event()
    s = Scheduler(repo, lambda a: seen.set(), interval=0.02)
    s.start()
    assert seen.wait(3)
    assert s.stop(timeout=3) is True


def test_claim_alert_true_for_exactly_one_caller_even_when_both_saw_it_pending(tmp_path):
    db = tmp_path / "race.db"
    a, b = Repository(db), Repository(db)
    it = a.add("reminder", "race", NOW, NOW)
    seen_a, seen_b = a.pending_alerts(NOW), b.pending_alerts(NOW)     # both read before either claims
    assert len(seen_a) == len(seen_b) == 1
    results = [a.claim_alert(it.id, it.effective_due), b.claim_alert(it.id, it.effective_due)]
    assert results == [True, False]
    # a snooze creates a NEW due time, which can be claimed once more
    a.snooze(it.id, NOW, minutes=5)
    new = a.get(it.id).effective_due
    assert a.claim_alert(it.id, new) is True and b.claim_alert(it.id, new) is False
    # claiming a stale/wrong due time never succeeds
    assert a.claim_alert(it.id, NOW) is False
