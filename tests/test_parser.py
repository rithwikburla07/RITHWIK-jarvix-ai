from datetime import date, time
from tests.helpers import NOW, make_parser

P = make_parser()


def parse(t):
    return P.parse(t, NOW)


def test_open_synonyms_and_fillers():
    for t in ["Open Notepad.", "launch notepad", "start Notepad", "run notepad", "fire up notepad",
              "Jarvix, can you please open notepad?", "Hey Jarvis, please open the notepad app",
              "I want you to open Notepad", "open notepad please"]:
        i = parse(t)
        assert (i.name, i.get("app")) == ("open_app", "Notepad"), t


def test_chrome_polite():
    i = parse("Jarvix, can you please launch Chrome?")
    assert (i.name, i.get("app")) == ("open_app", "Chrome")


def test_fuzzy_app_and_alias():
    assert parse("open crome").get("app") == "Chrome"
    assert parse("open vscode").get("app") == "VS Code"
    assert parse("launch cmd").get("app") == "Command Prompt"


def test_unknown_app_gives_suggestions():
    i = parse("open frobnicator9000")
    assert i.name == "unknown" and i.get("suggestions")


def test_volume_paths():
    for t in ["Increase the volume to 50 percent.", "volume 50", "set volume to 50%"]:
        i = parse(t)
        assert (i.name, i.get("level")) == ("set_volume", 50), t
    assert parse("mute").name == "mute" and parse("unmute").name == "unmute"
    assert parse("turn the volume down").name == "volume_down"
    assert parse("volume up").name == "volume_up"
    assert parse("volume 250").get("level") == 100


def test_reminder_full():
    i = parse("Remind me to call Mom tomorrow at 7 PM.")
    assert i.name == "create_reminder" and i.get("title") == "call Mom"
    assert i.get("date") == date(2026, 11, 13) and i.get("time") == time(19, 0)


def test_reminder_time_first_title_kept():
    assert parse("Remind me at 7 PM to call Mom").get("title") == "call Mom"
    assert parse("remind me in 10 minutes to stretch").get("title") == "stretch"


def test_workflows():
    assert parse("Start Work mode").get("workflow") == "Work"
    assert parse("Run Study workflow").get("workflow") == "Study"
    assert parse("Create workflow Deep Work").get("name") == "Deep Work"


def test_tasks_commands():
    assert parse("Show my tasks for today.").get("range") == "today"
    assert parse("what are my reminders for tomorrow").get("range") == "tomorrow"
    assert parse("show my tasks this week").get("range") == "week"
    assert parse("Mark task 3 as done.").get("id") == 3
    s = parse("Snooze my reminder for 10 minutes.")
    assert s.name == "snooze" and s.get("minutes") == 10 and s.get("id") is None
    assert parse("snooze reminder 4 for an hour").get("id") == 4
    d = parse("Delete reminder 2")
    assert d.name == "delete_item" and d.get("id") == 2


def test_web_music_system():
    y = parse("Open YouTube and play lo-fi music.")
    assert (y.name, y.get("query")) == ("youtube_search", "lo-fi music")
    w = parse("Search the web for Python dataclasses.")
    assert (w.name, w.get("query")) == ("web_search", "Python dataclasses")
    assert parse("What's my battery?").get("kind") == "battery"
    assert parse("how much storage do I have").get("kind") == "storage"
    assert parse("pause music").name == "music_pause"
    assert parse("play jazz").name == "music_play"


def test_gmail():
    assert parse("Check my unread emails.").name == "gmail_unread"
    assert parse("show my gmail drafts").name == "gmail_drafts"
    assert parse("read my latest email").name == "gmail_latest"


def test_whatsapp_exact_text():
    m = parse("Message Mom: I'm on my way")
    assert m.name == "whatsapp_message" and m.get("contact") == "Mom" and m.get("message") == "I'm on my way"
    q = parse("Send 'I'll be home at 7' to Mom on WhatsApp")
    assert q.get("message") == "I'll be home at 7" and q.get("contact") == "Mom"
    assert parse("Message Mom: I\u2019m late.").get("message") == "I\u2019m late."   # exact, curly quote kept
    assert parse('Send "Hello,  World!" to Dad on WhatsApp').get("message") == "Hello,  World!"
    c = parse("Call Mom on WhatsApp.")
    assert c.name == "whatsapp_call" and c.get("contact") == "Mom"
    assert parse("read my whatsapp messages").name == "whatsapp_unsupported"


def test_ambiguous_contact_flagged():
    i = parse("Message Al: hi")
    assert i.name == "whatsapp_message"


def test_phone_commands():
    assert parse("Open Instagram on my phone").name == "phone_open_app"
    assert parse("Ring my phone").name == "phone_ring"


def test_cleanup_and_refusals():
    assert parse("Clean my Downloads of old installers.").name == "clean_downloads"
    assert parse("force close chrome").name == "force_close_refused"


def test_control_intents():
    assert parse("pause jarvix").name == "jarvix_pause"
    assert parse("resume jarvix").name == "jarvix_resume"


def test_site_shortcut():
    assert parse("open youtube").name == "open_site"


def test_unknown_and_weird_input_never_crashes():
    for t in ["hello there", "", "   ", "!!!", "x" * 6000, "\x00\x01\x02", "\u202e\u0644\u0627", "open", "remind me", None, 123]:
        i = P.parse(t, NOW)
        assert i.name in ("unknown", "create_reminder", "open_app"), (t, i.name)
    assert parse("blah blah").get("suggestions")
