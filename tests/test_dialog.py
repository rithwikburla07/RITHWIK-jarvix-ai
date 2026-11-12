from datetime import datetime
from jarvis.core.dialog import DialogManager
from tests.helpers import NOW, make_parser


def mk():
    return DialogManager(make_parser())


def test_remind_then_time_followup():
    d = mk()
    r = d.handle("Remind me to call Mom", NOW)
    assert (r.kind, r.question_key, r.question) == ("ask", "ask.time", "What time?")
    r = d.handle("Tomorrow at 7 PM", NOW)
    assert r.kind == "intent" and r.intent.get("title") == "call Mom"
    assert r.intent.get("due_at") == datetime(2026, 11, 13, 19, 0) and d.pending is None


def test_reversed_order_followup_keeps_title():
    d = mk()
    d.handle("Remind me to call Mom", NOW)
    r = d.handle("7 PM tomorrow", NOW)
    assert r.intent.get("title") == "call Mom" and r.intent.get("due_at") == datetime(2026, 11, 13, 19, 0)


def test_full_reminder_needs_no_question():
    r = mk().handle("Remind me to call Mom tomorrow at 7 PM.", NOW)
    assert r.kind == "intent" and r.intent.get("due_at") == datetime(2026, 11, 13, 19, 0)


def test_one_question_per_turn_title_then_time():
    d = mk()
    assert d.handle("remind me", NOW).question_key == "ask.title"
    r = d.handle("water the plants", NOW)
    assert r.kind == "ask" and r.question_key == "ask.time"
    assert d.handle("at 6 PM", NOW).intent.get("title") == "water the plants"


def test_ambiguous_meridiem():
    d = mk()
    assert d.handle("remind me at 7 to stretch", NOW).question_key == "ask.meridiem"
    r = d.handle("pm", NOW)
    assert r.intent.get("due_at") == datetime(2026, 11, 12, 19, 0)


def test_cancel_clears_pending():
    d = mk()
    d.handle("Remind me to call Mom", NOW)
    assert d.handle("cancel", NOW).kind == "cancelled" and d.pending is None
    assert d.handle("cancel", NOW).kind == "nothing_to_cancel"


def test_expiry_drops_pending():
    d = mk()
    d.handle("Remind me to call Mom", NOW)
    r = d.handle("7 PM", datetime(2026, 11, 12, 10, 10))   # 10 min later > 120 s
    assert d.pending is None and r.intent.name != "create_reminder"


def test_new_command_replaces_pending():
    d = mk()
    d.handle("Remind me to call Mom", NOW)
    r = d.handle("mute", NOW)
    assert r.kind == "intent" and r.intent.name == "mute" and d.pending is None


def test_past_time_asks_again():
    r = mk().handle("remind me to x today at 9 AM", NOW)
    assert r.kind == "ask" and r.question_key == "ask.time_past"


def test_gives_up_after_repeated_nonsense():
    d = mk()
    d.handle("Remind me to call Mom", NOW)
    kinds = [d.handle("???", NOW).kind for _ in range(3)]
    assert kinds[-1] == "cancelled" and d.pending is None


def test_message_missing_text_kept_exact():
    d = mk()
    assert d.handle("Message Mom", NOW).question_key == "ask.message"
    r = d.handle("I'm late!  Sorry.", NOW)
    assert r.kind == "intent" and r.intent.get("message") == "I'm late!  Sorry."


def test_unknown_contact_asks_who():
    d = mk()
    r = d.handle("Message Zebediah: hi", NOW)
    assert r.question_key == "ask.contact"
    assert d.handle("Dad", NOW).intent.get("contact") == "Dad"


def test_snooze_missing_duration():
    d = mk()
    assert d.handle("Snooze my reminder", NOW).question_key == "ask.duration"
    assert d.handle("15 minutes", NOW).intent.get("minutes") == 15


def test_unknown_input_returns_suggestions_not_crash():
    r = mk().handle("flibbertigibbet", NOW)
    assert r.kind == "intent" and r.intent.name == "unknown" and r.intent.get("suggestions")
