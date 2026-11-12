from jarvis.core.redact import redact_intent, redact_text


def test_phone_email_token_pairing():
    t = redact_text("call +91 98765 43210 or a.b@example.com token=abc123secret Bearer abcdefgh12345 pairing code: XK29-77QA")
    for leak in ("98765", "a.b@example.com", "abc123secret", "abcdefgh12345", "XK29"):
        assert leak not in t, leak


def test_dates_and_times_survive():
    assert redact_text("due 2026-11-12 at 7 PM") == "due 2026-11-12 at 7 PM"


def test_links_redacted():
    t = redact_text("https://wa.me/919876543210?text=hello%20there")
    assert "9198765" not in t and "hello" not in t
    g = redact_text("https://mail.google.com/mail/?view=cm&to=a@b.com&su=Secret&body=Hi")
    assert "a@b.com" not in g and "Secret" not in g


def test_google_secret_shapes():
    assert "GOCSPX" not in redact_text("GOCSPX-abcdefghijklmnop")
    assert "ya29" not in redact_text("ya29.a0AfH6SMBxyz")


def test_intent_logging_hides_bodies():
    s = redact_intent("whatsapp_message", {"contact": "Mom", "message": "my secret plan", "kind": "x"})
    assert "secret plan" not in s and "Mom" not in s


def test_timestamps_in_intent_logs_survive():
    s = redact_intent("create_reminder", {"title": "x", "due_at": "2026-11-13 19:00:00", "date": "2026-11-13"})
    assert "2026-11-13 19:00:00" in s and "<phone>" not in s
    assert "<phone>" in redact_text("call 2026-11-13 then +44 20 7946 0958")
