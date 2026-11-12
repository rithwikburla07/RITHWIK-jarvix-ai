from datetime import datetime, timedelta

from jarvis.core.engine import Engine
from jarvis.tasks.repository import Repository
from jarvis.tasks.scheduler import Scheduler
from jarvis.tasks.service import TaskService
from tests.helpers import NOW, make_parser


def mk():
    repo = Repository()
    return Engine(make_parser(), TaskService(repo)), repo


def say(e, text, source="typed", now=NOW):
    return e.handle(text, source=source, now=now)


def test_reminder_full_phrase_saves_title_and_local_time():
    e, repo = mk()
    r = say(e, "Remind me to call Mom tomorrow at 7 PM.")
    assert r.kind == "done" and "call Mom" in r.text
    it = repo.get(1)
    assert (it.type, it.title, it.due_at) == ("reminder", "call Mom", datetime(2026, 11, 13, 19, 0))


def test_reminder_two_turn_flow_keeps_title():
    e, repo = mk()
    assert say(e, "Remind me to call Mom").kind == "ask"
    r = say(e, "7 PM tomorrow")
    assert r.kind == "done" and repo.get(1).title == "call Mom"
    assert repo.get(1).due_at == datetime(2026, 11, 13, 19, 0)


def test_show_tasks_for_today_and_empty_state():
    e, repo = mk()
    assert "nothing scheduled" in say(e, "Show my tasks for today.").text
    say(e, "Remind me to stretch today at 6 PM")
    say(e, "add task buy milk")
    out = say(e, "Show my tasks for today.").text
    assert "stretch" in out and "buy milk" in out and "today at 6:00 PM" in out


def test_mark_task_done_and_errors():
    e, repo = mk()
    say(e, "add task one"); say(e, "add task two"); say(e, "add task three")
    assert say(e, "Mark task 3 as done.").kind == "done" and repo.get(3).status == "done"
    assert repo.get(1).status == "open"
    assert "already done" in say(e, "Mark task 3 as done").text
    assert say(e, "Mark task 99 as done").kind == "error"


def test_delete_waits_for_confirmation_then_soft_deletes():
    e, repo = mk()
    say(e, "Remind me to a today at 6 PM"); say(e, "Remind me to b today at 7 PM")
    r = say(e, "Delete reminder 2")
    assert r.kind == "confirm" and repo.get(2).status == "open" and e.awaiting_confirmation
    assert say(e, "yes").kind == "done" and repo.get(2).status == "deleted" and repo.get(1).status == "open"


def test_delete_declined_or_cancelled_changes_nothing():
    e, repo = mk()
    say(e, "Remind me to a today at 6 PM")
    say(e, "Delete reminder 1"); say(e, "no")
    assert repo.get(1).status == "open" and not e.awaiting_confirmation
    say(e, "Delete reminder 1"); say(e, "cancel")
    assert repo.get(1).status == "open"


def test_voice_and_automation_cannot_confirm_delete():
    e, repo = mk()
    say(e, "Remind me to a today at 6 PM")
    say(e, "Delete reminder 1")
    for src in ("voice", "workflow", "brain", "phone"):
        r = say(e, "yes", source=src)
        assert r.kind == "refused" and repo.get(1).status == "open", src
    assert e.awaiting_confirmation                      # still waiting for a typed/UI answer
    assert e.confirm(source="voice").kind == "refused"
    assert e.confirm(source="ui", now=NOW).kind == "done" and repo.get(1).status == "deleted"


def test_stale_confirmation_expires_and_other_command_cancels_it():
    e, repo = mk()
    say(e, "Remind me to a today at 6 PM")
    say(e, "Delete reminder 1")
    late = NOW + timedelta(seconds=61)
    assert say(e, "yes", now=late).kind == "error" and repo.get(1).status == "open"
    say(e, "Delete reminder 1")
    say(e, "show my tasks for today")                   # different command -> old confirmation is stale
    assert say(e, "yes").kind != "done" and repo.get(1).status == "open"


def test_delete_wrong_kind_or_missing_is_refused_without_prompt():
    e, repo = mk()
    say(e, "add task t")
    r = say(e, "Delete reminder 1")
    assert r.kind == "error" and not e.awaiting_confirmation and repo.get(1).status == "open"
    assert say(e, "Delete reminder 42").kind == "error"


def test_snooze_single_candidate_and_none():
    e, repo = mk()
    assert say(e, "Snooze my reminder for 10 minutes.").kind == "error"      # nothing has fired
    a = repo.add("reminder", "stretch", NOW - timedelta(minutes=1), NOW - timedelta(hours=1))
    Scheduler(repo, lambda al: None).tick(NOW)
    r = say(e, "Snooze my reminder for 10 minutes.")
    assert r.kind == "done" and repo.get(a.id).snoozed_until == datetime(2026, 11, 12, 10, 10)


def test_snooze_multiple_candidates_asks_which():
    e, repo = mk()
    a = repo.add("reminder", "first", NOW - timedelta(minutes=2), NOW - timedelta(hours=1))
    b = repo.add("reminder", "second", NOW - timedelta(minutes=1), NOW - timedelta(hours=1))
    Scheduler(repo, lambda al: None).tick(NOW)
    r = say(e, "Snooze my reminder for 10 minutes")
    assert r.kind == "ask" and "first" in r.text and "second" in r.text
    assert say(e, "banana").kind == "ask"                                    # re-asks politely
    assert say(e, str(a.id)).kind == "done"
    assert repo.get(a.id).snoozed_until is not None and repo.get(b.id).snoozed_until is None


def test_snooze_choice_can_be_cancelled_or_replaced():
    e, repo = mk()
    for n in ("first", "second"):
        repo.add("reminder", n, NOW - timedelta(minutes=1), NOW - timedelta(hours=1))
    Scheduler(repo, lambda al: None).tick(NOW)
    say(e, "snooze for 5 minutes"); assert say(e, "cancel").kind == "info"
    say(e, "snooze for 5 minutes"); assert say(e, "show my tasks for today").kind == "info"
    assert all(i.snoozed_until is None for i in repo.recently_alerted_open(NOW))


def test_snooze_explicit_id():
    e, repo = mk()
    a = repo.add("reminder", "x", NOW + timedelta(hours=1), NOW)
    assert say(e, f"snooze reminder {a.id} for an hour").kind == "done"
    assert repo.get(a.id).snoozed_until == datetime(2026, 11, 12, 11, 0)


def test_reschedule_flow():
    e, repo = mk()
    a = repo.add("reminder", "x", NOW + timedelta(hours=1), NOW)
    assert say(e, f"reschedule reminder {a.id}").key == "ask.time"
    assert say(e, "tomorrow at 9 AM").kind == "done"
    assert repo.get(a.id).due_at == datetime(2026, 11, 13, 9, 0)


def test_pause_cancels_pending_and_blocks_until_resume():
    e, repo = mk()
    say(e, "Remind me to a today at 6 PM")
    say(e, "Delete reminder 1")
    assert say(e, "pause jarvix").kind == "info" and e.paused and not e.awaiting_confirmation
    assert say(e, "yes").kind == "info" and repo.get(1).status == "open"
    assert say(e, "add task nope").kind == "info" and len(repo.undated_open()) == 0
    assert say(e, "resume jarvix").text == "Jarvix resumed." and not e.paused
    assert say(e, "add task ok").kind == "done"


def test_pause_clears_dialog_question():
    e, repo = mk()
    assert say(e, "Remind me to call Mom").kind == "ask"
    e.pause(); e.resume()
    r = say(e, "7 PM")                                  # no pending question any more
    assert r.kind != "done" and repo.undated_open() == []


def test_unsupported_and_unknown_never_crash():
    e, _ = mk()
    assert say(e, "open notepad").kind == "unavailable"
    assert say(e, "flibbertigibbet").kind == "unknown"
    assert say(e, "").kind == "unknown" and say(e, "x" * 5000).kind == "unknown"
    assert say(e, "force close chrome").kind == "refused"
    assert say(e, "exit jarvix").kind == "exit"


def test_cancel_clears_pending_state():
    e, _ = mk()
    say(e, "Remind me to call Mom")
    assert say(e, "cancel").key == "cancel.done" and e.dialog.pending is None
    assert say(e, "cancel").key == "cancel.none"
