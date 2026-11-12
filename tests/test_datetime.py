from datetime import date, datetime, time
from jarvis.tasks.datetime_parser import apply_meridiem, parse_duration, parse_when, resolve_due
from tests.helpers import NOW


def due(text):
    p = parse_when(text, NOW)
    return resolve_due(p.date, p.time, p.relative_dt, NOW)


def test_fixed_now_is_thursday():
    assert date(2026, 11, 12).weekday() == 3


def test_tomorrow_at_7pm():
    assert due("tomorrow at 7 PM") == datetime(2026, 11, 13, 19, 0)


def test_time_only_future_is_today_and_past_rolls_to_tomorrow():
    assert due("7 PM") == datetime(2026, 11, 12, 19, 0)
    assert due("9 AM") == datetime(2026, 11, 13, 9, 0)


def test_in_10_minutes_and_hours():
    assert due("in 10 minutes") == datetime(2026, 11, 12, 10, 10)
    assert due("in an hour") == datetime(2026, 11, 12, 11, 0)
    assert parse_when("in 2 days", NOW).date == date(2026, 11, 14)


def test_weekdays():
    assert parse_when("next Friday", NOW).date == date(2026, 11, 13)
    assert parse_when("on monday", NOW).date == date(2026, 11, 16)
    assert parse_when("thursday", NOW).date == date(2026, 11, 12)
    assert parse_when("next thursday", NOW).date == date(2026, 11, 19)


def test_explicit_date_and_rollover():
    assert due("on Nov 20 at 9:30 AM") == datetime(2026, 11, 20, 9, 30)
    assert parse_when("Nov 5", NOW).date == date(2027, 11, 5)
    assert parse_when("20 November", NOW).date == date(2026, 11, 20)


def test_invalid_and_ambiguous():
    assert parse_when("Feb 30", NOW).invalid
    assert parse_when("at 7", NOW).ambiguous_hour == (7, 0)
    assert parse_when("at 25", NOW).invalid
    assert apply_meridiem((7, 0), "PM") == time(19, 0)


def test_24h_noon_midnight():
    assert due("19:30") == datetime(2026, 11, 12, 19, 30)
    assert due("tomorrow at noon") == datetime(2026, 11, 13, 12, 0)
    assert due("tomorrow at midnight") == datetime(2026, 11, 13, 0, 0)


def test_naive_local_time_only():
    assert due("tomorrow at 7 PM").tzinfo is None


def test_remainder_keeps_title():
    assert parse_when("call Mom tomorrow at 7 PM", NOW).remainder == "call Mom"


def test_duration():
    assert parse_duration("for 10 minutes") == 10
    assert parse_duration("an hour") == 60
    assert parse_duration("2 hrs") == 120
    assert parse_duration("soon") is None
