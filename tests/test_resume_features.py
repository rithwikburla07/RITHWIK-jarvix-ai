"""Regression coverage for the resumed Windows build and the phone protocol."""
from datetime import datetime, timedelta
import json
import os
import re
import stat
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from jarvis.config.settings import Settings, write_json
from jarvis.core.engine import Engine
from jarvis.core.intents import Intent, Reply
from jarvis.core.parser import Parser, default_catalog
from jarvis.i18n.locale import LANGUAGES, Locale
from jarvis.integrations.brain import endpoint
from jarvis.integrations.gmail import Gmail, compose_url
from jarvis.integrations.phone import private_ip
from jarvis.integrations.phone_protocol import LinkError, MAX_FILE, Pairing, Source, fingerprint
from jarvis.integrations.web import search_url
from jarvis.integrations.whatsapp import WhatsApp, whatsapp_url
from jarvis.maintenance.inventory import MANIFEST, checked, sha256
from jarvis.maintenance.uninstall import apply, build, run
from jarvis.system.desktop import DesktopFiles
from jarvis.tasks.locking import SingleInstanceLock
from jarvis.voice.backends import SpeechRecognizer, VoiceError, wake_command
from jarvis.workflows.loader import WorkflowError, validate_url
from jarvis.workflows.runner import CancelToken, WorkflowRunner
from jarvis.workflows.service import WorkflowService

NOW = datetime(2026, 11, 12, 14)


def test_settings_survive_restart(tmp_path):
    path = tmp_path / "settings.json"
    first = Settings(path)
    first.update(language="de", reduced_motion=True)
    with pytest.raises(ValueError):
        first.update(speech_volume=101)
    second = Settings(path)
    assert second.get("language") == "de" and second.get("reduced_motion") is True
    assert second.get("voice_provider") == "off" and second.get("brain_enabled") is False


@pytest.mark.parametrize("language", LANGUAGES)
def test_locale_parses_essential_commands(language):
    locale = Locale(language)
    p = Parser(default_catalog())
    p.locale = locale
    assert locale.data["ui"].keys() == Locale().data["ui"].keys()
    assert p.parse(locale.data["examples"][0], NOW).get("app") == "Notepad"
    assert p.parse(locale.data["examples"][1], NOW).get("level") == 50
    assert p.parse(locale.data["examples"][2], NOW).name == "show_tasks"
    assert p.parse(locale.data["examples"][3], NOW).get("kind") == "battery"


@pytest.mark.parametrize(("language", "text"), [
    ("hi", "याद दिलाओ अध्ययन कल १९:००"),
    ("ru", "напомни мне учёба завтра в 19:00"),
    ("uk", "нагадай мені навчання завтра о 19:00"),
    ("pt", "lembre-me estudar amanhã às 19:00"),
    ("tr", "hatırlat ders yarın saat 19:00"),
    ("it", "ricordami studiare domani alle 19:00"),
    ("de", "erinnere mich lernen morgen um 19:00"),
])
def test_localized_due_date(language, text):
    parser = Parser()
    parser.locale = Locale(language)
    from jarvis.core.dialog import DialogManager
    result = DialogManager(parser).handle(text, NOW)
    assert result.intent.get("due_at") == datetime(2026, 11, 13, 19)


def test_safe_search_encoding_and_url_validation():
    assert parse_qs(urlsplit(search_url("a & b")).query) == {"q": ["a & b"]}
    assert parse_qs(urlsplit(search_url("a & b", "youtube")).query) == {"search_query": ["a & b"]}
    for value in ("file:///C:/notes", "https://user@domain.test/", r"https://example.test\@bad.test/"):
        with pytest.raises(WorkflowError):
            validate_url(value)


def test_cleanup_requires_exact_fresh_typed_yes(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    old = downloads / "old.exe"
    old.write_bytes(b"disposable installer fixture")
    ts = (NOW - timedelta(days=100)).timestamp()
    os.utime(old, (ts, ts))
    keep = downloads / "new.msi"
    keep.touch()
    ts = NOW.timestamp()
    os.utime(keep, (ts, ts))
    from jarvis.core import fileplan
    fake_bin = tmp_path / "test-bin"
    fake_bin.mkdir()
    monkeypatch.setattr(fileplan, "default_trasher", lambda: lambda source: os.rename(
        source, fake_bin / Path(source).name
    ))
    p = Parser(default_catalog())
    e = Engine(p)
    files = DesktopFiles(SimpleNamespace(entries=[]), lambda _: True, Settings())
    e.handlers["clean_downloads"] = files.cleanup
    preview = e.handle("Clean my Downloads of old installers", now=NOW)
    assert preview.kind == "confirm" and "old.exe" in preview.text and "new.msi" not in preview.text
    assert e.handle("yes", source="voice", now=NOW).kind == "refused"
    assert e.handle("yes", source="ui", now=NOW).kind == "refused"
    assert e.handle("yes.", now=NOW).kind == "refused"
    assert old.exists()
    assert e.handle("yes", now=NOW).kind == "done"
    assert keep.exists() and (fake_bin / old.name).is_file()
    assert e.confirm(now=NOW).kind == "info"


def test_search_skips_unapproved_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    docs = tmp_path / "Documents"
    docs.mkdir()
    report = docs / "report.txt"
    report.touch()
    (tmp_path / "private-report.txt").touch()
    opened = []
    files = DesktopFiles(SimpleNamespace(entries=[]), lambda path: (opened.append(path), True)[1], Settings())
    reply, _ = files.find(Intent("file_search", {"query": "report"}), NOW)
    assert [hit["path"] for hit in reply.data["hits"]] == [str(report)]
    reply, _ = files.open_hit(Intent("reveal_search_hit", {"index": 0}), NOW)
    assert reply.kind == "done" and opened == [str(docs)]


def test_workflow_dialogue_persists_and_renames(tmp_path):
    parser = Parser(default_catalog())
    engine = Engine(parser)
    runner = WorkflowRunner(lambda *_: Reply("done", "ok"), lambda _: True, lambda _: True)
    path = tmp_path / "flows.json"
    engine.workflows = service = WorkflowService({}, runner, path, parser.cat)
    assert engine.handle("Create workflow Deep Work", now=NOW).kind == "ask"
    assert engine.handle("add open_app Notepad", now=NOW).kind == "ask"
    assert engine.handle("save workflow", now=NOW).key == "workflow.saved"
    service.save("Deep Work", {"name": "My Work", "steps": [{"type": "notify", "text": "ready"}]})
    assert WorkflowService({}, runner, path).names() == ["My Work"]
    assert parser.parse("run My Work workflow", NOW).get("workflow") == "My Work"


def test_workflow_wait_is_cancellable():
    token = CancelToken()
    runner = WorkflowRunner(lambda *_: Reply("done", "ok"), lambda _: True, lambda _: True)
    timer = threading.Timer(.05, token.cancel)
    timer.start()
    start = time.monotonic()
    try:
        result = runner.run({"name": "Wait", "steps": [{"type": "pause", "seconds": 30}]}, NOW, token)
    finally:
        timer.cancel()
    assert result.cancelled and result.steps[0].status == "skipped"
    assert time.monotonic() - start < 2


def test_recognizer_reports_provider_failure_and_cancels(monkeypatch):
    class RequestError(Exception):
        pass
    class UnknownValueError(Exception):
        pass
    class Recognizer:
        def recognize_google(self, audio, *, language):
            assert language == "hi-IN"
            raise RequestError("synthetic")
    fake = SimpleNamespace(RequestError=RequestError, UnknownValueError=UnknownValueError,
                           Recognizer=Recognizer, AudioData=lambda *args: args)
    monkeypatch.setitem(sys.modules, "speech_recognition", fake)
    token = threading.Event()
    with pytest.raises(VoiceError, match="unavailable"):
        SpeechRecognizer().recognize(b"x" * 4000, 16000, "google", "", "hi", token)
    token.set()
    assert SpeechRecognizer().recognize(b"", 16000, "vosk", "", "en", token) == ""
    assert wake_command("Hey Jarvix, open Notepad") == "open Notepad"
    assert wake_command("ordinary conversation") is None


def test_local_brain_endpoint_is_literal_loopback_only():
    assert endpoint("http://localhost:11434") == ("127.0.0.1", 11434, "/api/generate")
    assert endpoint("http://[::1]:9000/v1/chat/completions")[0] == "::1"
    for value in ("https://127.0.0.1:11434", "http://127.0.0.1.example.com/",
                  "http://192.168.1.2/", "http://127.0.0.1@remote.test/"):
        with pytest.raises(ValueError):
            endpoint(value)


def test_message_handoffs_use_exact_encoded_content(tmp_path):
    contact = "I'm on my way & bringing tea."
    assert parse_qs(urlsplit(whatsapp_url("+91 12345 67890", contact)).query)["text"] == [contact]
    assert parse_qs(urlsplit(compose_url("user@example.com", "A & B", contact)).query)["body"] == [contact]
    e = Engine(Parser(default_catalog()))
    urls = []
    wa = WhatsApp(tmp_path / "contacts.json", e.parser.cat, Settings(), lambda url: (urls.append(url), True)[1])
    wa.save_contact(Intent("whatsapp_contact", {"name": "Mom", "number": "+91 12345 67890"}), NOW)
    e.handlers["whatsapp_message"] = wa.handle
    reply = e.handle("Message Mom: " + contact, now=NOW)
    assert reply.kind == "confirm" and contact in reply.text and not urls
    assert e.handle("yes", source="phone", now=NOW).kind == "refused"
    assert not urls and e.handle("yes", now=NOW).kind == "done"
    assert len(urls) == 1 and urlsplit(urls[0]).netloc == "wa.me"


def test_gmail_read_setup_and_browser_review(tmp_path):
    urls = []
    gmail = Gmail(tmp_path / "accounts.json", lambda url: (urls.append(url), True)[1])
    reply, followup = gmail.read(Intent("gmail_unread"), NOW)
    assert reply.kind == "unavailable" and followup is None
    e = Engine(Parser())
    e.handlers["gmail_compose"] = gmail.compose
    preview = e.ui_intent(Intent("gmail_compose", {"to": "you@example.com", "subject": "Review",
                                                   "body": "Exact text"}), NOW)
    assert preview.kind == "confirm" and "Exact text" in preview.text and not urls
    assert e.handle("yes", source="model", now=NOW).kind == "refused"
    assert e.handle("yes", now=NOW).kind == "done"
    assert urls == [compose_url("you@example.com", "Review", "Exact text")]


def test_gmail_reads_real_fixture_count_and_plain_message(tmp_path, monkeypatch):
    calls = []
    def fixture(token, path, _params=None):
        calls.append((token, path))
        return {"messagesUnread": 4}
    gmail = Gmail(tmp_path / "gmail.json", lambda _: False, get=fixture)
    gmail.selected = "fake"
    monkeypatch.setattr(gmail, "_token", lambda: "test-token")
    reply, _ = gmail.read(Intent("gmail_unread"), NOW)
    assert "4" in reply.text and calls == [("test-token", "labels/INBOX")]


def test_pair_invitation_once_expiry_replay_and_revocation():
    clock = [1000.]
    hub = Pairing(clock=lambda: clock[0])
    token = hub.new_invitation()
    account = hub.pair(token, "Test handset")
    with pytest.raises(LinkError):
        hub.pair(token, "Replay")
    request = {**account, "session": hub.session, "nonce": "a" * 32, "time": 1000}
    assert hub.authenticate(request) == account["device"]
    with pytest.raises(LinkError, match="replay"):
        hub.authenticate(request)
    hub.new_session()
    with pytest.raises(LinkError, match="session"):
        hub.authenticate(request)
    hub.revoke(account["device"])
    with pytest.raises(LinkError):
        hub.authenticate({**request, "session": hub.session})
    expired = hub.new_invitation()
    clock[0] += 121
    with pytest.raises(LinkError):
        hub.pair(expired, "Expired")
    assert private_ip("192.168.1.5") == "192.168.1.5"
    with pytest.raises(LinkError):
        private_ip("0.0.0.0")


def test_transfer_size_approval_retry_cancel_and_hash(tmp_path, monkeypatch):
    def sized(size):
        return SimpleNamespace(st_size=size, st_mode=stat.S_IFREG, st_dev=1, st_ino=1, st_mtime_ns=1)
    assert fingerprint(sized(MAX_FILE))[2] == 2_000_000_000
    with pytest.raises(LinkError):
        fingerprint(sized(MAX_FILE + 1))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    documents = tmp_path / "Documents"
    documents.mkdir()
    path = documents / "fixture.txt"
    path.write_bytes(b"safe fixture")
    source = Source.prepare(path, [str(documents)], threading.Event(), lambda *_: None)
    assert source.sha256 == sha256(path)
    hub = Pairing()
    device = hub.pair(hub.new_invitation(), "Test phone")["device"]
    ident = hub.enqueue(device, "transfer", {"size": source.size}, source)
    with pytest.raises(LinkError):
        hub.begin_stream(device, ident)
    hub.accept(device, ident)
    job = hub.begin_stream(device, ident)
    hub.end_stream(job, False)
    job = hub.begin_stream(device, ident)
    hub.end_stream(job, True)
    with pytest.raises(LinkError, match="integrity"):
        hub.finish(device, ident, True, "0" * 64)
    hub.finish(device, ident, True, source.sha256)
    assert job.status == "complete"
    second = hub.enqueue(device, "transfer", {}, source)
    assert hub.cancel(second)
    with pytest.raises(LinkError):
        hub.begin_stream(device, second)


def test_windows_scheduler_lock_rejects_second_instance(tmp_path):
    path = tmp_path / "lease.lock"
    first, second = SingleInstanceLock(path), SingleInstanceLock(path)
    assert first.acquire()
    try:
        assert not second.acquire()
    finally:
        first.release()
    assert second.acquire()
    second.release()


def test_uninstall_preview_and_changed_or_unknown_files_retained(tmp_path):
    root = tmp_path / "application"
    profile = tmp_path / "profile"
    root.mkdir()
    profile.mkdir()
    source = root / "main.py"
    source.write_text("owned", encoding="utf-8")
    manifest = root / MANIFEST
    write_json(manifest, {"product": "Jarvix Personal AI", "files": {"main.py": sha256(source)}})
    unknown = root / "private.txt"
    unknown.write_text("keep", encoding="utf-8")
    saved = profile / "settings.json"
    write_json(saved, {"title": "Saved profile"})
    assert run(root, profile, ["uninstall", "--dry-run"], read=lambda _: pytest.fail("prompted"),
               write=lambda _: None) == 0
    plan, _ = build(root, profile)
    source.write_text("modified after review", encoding="utf-8")
    removed, retained, _locked = apply(plan)
    assert str(source) in retained and source.exists() and unknown.exists() and saved.exists()
    with pytest.raises(ValueError):
        checked(root, "data/../../private.txt")
