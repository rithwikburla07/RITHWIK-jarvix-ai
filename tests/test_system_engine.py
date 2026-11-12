"""Runs on your laptop (needs the Checkpoint 1 parser). Requires apply_checkpoint3.py to have been run."""
from jarvis.core.engine import Engine
from tests.helpers import NOW, make_parser
from tests.test_system_actions import make


def eng():
    acts, calls, popens = make()
    return Engine(make_parser(), system=acts), calls, popens


def test_open_notepad_launches_through_real_command_entry():
    e, calls, popens = eng()
    r = e.handle("Open Notepad.", now=NOW)
    assert r.kind == "done" and popens[-1][0] == ["C:\\Windows\\System32\\notepad.exe"]


def test_close_needs_typed_yes_and_voice_cannot_confirm():
    e, calls, _ = eng()
    assert e.handle("close notepad", now=NOW).kind == "confirm"
    assert e.handle("yes", source="voice", now=NOW).kind == "refused"
    assert calls == [] and e.awaiting_confirmation
    assert e.handle("yes", source="typed", now=NOW).kind == "done"
    assert calls[-1] == ["C:\\Windows\\System32\\taskkill.exe", "/IM", "notepad.exe"]


def test_close_declined_changes_nothing():
    e, calls, _ = eng()
    e.handle("close notepad", now=NOW)
    assert e.handle("no", now=NOW).kind == "info" and calls == []


def test_protected_app_cannot_be_closed_by_command():
    e, calls, _ = eng()
    assert e.handle("close calculator", now=NOW).kind == "refused" and calls == []


def test_volume_command_sets_level():
    acts, calls, _ = make()
    e = Engine(make_parser(), system=acts)
    r = e.handle("volume 50", now=NOW)
    assert r.kind == "done" and "50%" in r.text and acts.volume.get() == 50


def test_power_saver_asks_first_and_no_cancels():
    e, calls, _ = eng()
    assert e.handle("switch to power saver", now=NOW).kind == "confirm"
    assert calls == []
    e.handle("no", now=NOW)
    assert calls == []


def test_pause_blocks_system_actions_until_resume():
    e, calls, popens = eng()
    e.pause()
    assert e.handle("Open Notepad.", now=NOW).kind == "info" and popens == []
    e.handle("resume jarvix", now=NOW)
    assert e.handle("Open Notepad.", now=NOW).kind == "done"


def test_battery_query_reads_status():
    e, _, _ = eng()
    assert e.handle("What's my battery?", now=NOW).kind == "info"
