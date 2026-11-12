from datetime import datetime, timedelta

from jarvis.tasks import ics
from jarvis.tasks.repository import Repository
from jarvis.tasks.service import TaskService
from tests.helpers import NOW, raises


def test_export_shape_and_escaping():
    r = Repository()
    r.add("reminder", "buy milk, eggs; and\\more", NOW + timedelta(hours=9), NOW)
    r.add("task", "undated is skipped", None, NOW)
    text = TaskService(r).export_ics(NOW)
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert text.count("BEGIN:VEVENT") == 1 and "BEGIN:VALARM" in text
    assert "DTSTART:20261112T190000\r\n" in text
    assert "buy milk\\, eggs\\; and\\\\more" in text


def test_long_lines_are_folded_to_75_octets():
    r = Repository()
    r.add("task", "é" * 200, NOW + timedelta(hours=1), NOW)
    for line in TaskService(r).export_ics(NOW).split("\r\n"):
        assert len(line.encode("utf-8")) <= 75


def test_round_trip_preserves_title_time_type():
    r = Repository()
    r.add("reminder", "call Mom, then Dad; ok", datetime(2026, 11, 13, 19, 0), NOW)
    r.add("task", "write report", datetime(2026, 11, 14, 9, 30), NOW)
    events, warns = ics.parse_ics(TaskService(r).export_ics(NOW))
    got = {(e.title, e.due, e.is_reminder) for e in events}
    assert got == {("call Mom, then Dad; ok", datetime(2026, 11, 13, 19, 0), True),
                   ("write report", datetime(2026, 11, 14, 9, 30), False)}
    assert warns == []


def test_import_dedupes_and_is_repeatable():
    r1 = Repository(); r1.add("reminder", "x", datetime(2026, 11, 13, 19, 0), NOW)
    text = TaskService(r1).export_ics(NOW)
    r2 = Repository(); s2 = TaskService(r2)
    assert s2.import_ics(text, NOW).data["added"] == 1
    again = s2.import_ics(text, NOW)
    assert again.data["added"] == 0 and again.data["skipped"] == 1


def test_import_foreign_calendar_warnings():
    text = ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:All day\r\nDTSTART;VALUE=DATE:20261120\r\nEND:VEVENT\r\n"
            "BEGIN:VEVENT\r\nSUMMARY:Weekly\r\nDTSTART;TZID=Europe/Paris:20261120T100000\r\nRRULE:FREQ=WEEKLY\r\nEND:VEVENT\r\n"
            "BEGIN:VEVENT\r\nSUMMARY:No start\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nDTSTART:20261120T100000\r\nEND:VEVENT\r\n"
            "BEGIN:VEVENT\r\nSUMMARY:Bad\r\nDTSTART:garbage\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    events, warns = ics.parse_ics(text)
    assert [e.title for e in events] == ["All day", "Weekly"]
    assert events[0].due == datetime(2026, 11, 20, 9, 0) and events[1].due == datetime(2026, 11, 20, 10, 0)
    joined = " ".join(warns)
    assert "all-day" in joined and "recurring" in joined and "time zone" in joined and "skipped" in joined


def test_utc_times_convert_to_local_naive():
    ev, _ = ics.parse_ics("BEGIN:VEVENT\r\nSUMMARY:u\r\nDTSTART:20261120T100000Z\r\nEND:VEVENT\r\n")
    expect = datetime(2026, 11, 20, 10, 0).replace(tzinfo=__import__("datetime").timezone.utc).astimezone().replace(tzinfo=None)
    assert ev[0].due == expect and ev[0].due.tzinfo is None


def test_import_limits_and_hostile_input():
    assert raises(ValueError, ics.parse_ics, "x" * 1_000_001)
    many = "".join(f"BEGIN:VEVENT\r\nSUMMARY:e{i}\r\nDTSTART:20261120T100000\r\nEND:VEVENT\r\n" for i in range(1200))
    events, warns = ics.parse_ics(many)
    assert len(events) == 1000 and any("stopped" in w for w in warns)
    assert ics.parse_ics("not a calendar \x00\x01")[0] == []
    r = TaskService(Repository()).import_ics("x" * 1_000_001, NOW)
    assert r.kind == "error"
