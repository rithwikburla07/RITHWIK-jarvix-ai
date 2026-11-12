import json
from datetime import datetime, timedelta
from pathlib import Path

from jarvis.core.intents import Reply
from jarvis.workflows.loader import WorkflowError, load_workflows, validate_step, validate_workflow
from jarvis.workflows.runner import CancelToken, WorkflowRunner
from jarvis.workflows.service import WorkflowService, format_result

NOW = datetime(2026, 11, 12, 10, 0)
WF_FILE = Path(__file__).resolve().parents[1] / "data" / "workflows.json"
EXPECTED = {"Work", "Focus", "Study", "Research", "Planning", "Creative", "Morning",
            "Email", "Meeting", "Coding", "Music", "Finance"}


def bad(step):
    try:
        validate_step(step, 1)
    except WorkflowError:
        return
    raise AssertionError(f"accepted bad step: {step}")


def test_shipped_file_has_all_twelve_valid_workflows():
    wfs = load_workflows(WF_FILE)
    assert {w["name"] for w in wfs.values()} == EXPECTED
    assert all(1 <= len(w["steps"]) <= 10 for w in wfs.values())


def test_study_mode_has_study_page_25_minute_timer_and_notes_folder():
    study = load_workflows(WF_FILE)["study"]
    kinds = [(s["type"], s.get("minutes"), s.get("folder")) for s in study["steps"]]
    assert ("start_timer", 25, None) in kinds and ("open_folder", None, "notes") in kinds
    assert any(step["type"] == "open_study_page" for step in study["steps"])


def test_rejects_unknown_step_type_and_unexpected_fields():
    bad({"type": "run_shell", "cmd": "dir"})
    bad({"type": "open_app", "app": "Notepad", "args": ["/c", "del"]})
    bad({"type": "speak"})


def test_pause_is_bounded():
    bad({"type": "pause", "seconds": 31})
    bad({"type": "pause", "seconds": 0})
    assert validate_step({"type": "pause", "seconds": 30}, 1)["seconds"] == 30


def test_timer_and_volume_bounds_and_types():
    bad({"type": "start_timer", "minutes": 0, "label": "x"})
    bad({"type": "start_timer", "minutes": 481, "label": "x"})
    bad({"type": "set_volume", "level": 101})
    bad({"type": "set_volume", "level": True})      # bool is not a number here
    bad({"type": "set_volume", "level": "50"})


def test_urls_must_be_http_or_https():
    for url in ("file:///C:/Windows/notepad.exe", "javascript:alert(1)", "https://", "ms-settings:",
                "https://ok.example/ path", "ftp://example.org"):
        bad({"type": "open_url", "url": url})
    assert validate_step({"type": "open_url", "url": "https://mail.google.com"}, 1)["url"]


def test_folders_are_fixed_keys_not_paths():
    bad({"type": "open_folder", "folder": "C:\\Windows"})
    bad({"type": "open_folder", "folder": "..\\..\\Users"})
    assert validate_step({"type": "open_folder", "folder": "notes"}, 1)["folder"] == "notes"


def test_text_rejects_control_characters_and_long_values():
    bad({"type": "notify", "text": "hi\x00there"})
    bad({"type": "notify", "text": "x" * 201})
    bad({"type": "web_search", "query": "a"})


def test_workflow_step_limit():
    try:
        validate_workflow({"name": "Too big", "steps": [{"type": "notify", "text": "x"}] * 11})
    except WorkflowError:
        pass
    else:
        raise AssertionError("accepted 11 steps")


def test_duplicate_workflow_names_rejected(tmp_path):
    p = tmp_path / "w.json"
    p.write_text(json.dumps([{"name": "Work", "steps": [{"type": "notify", "text": "a"}]},
                             {"name": "work", "steps": [{"type": "notify", "text": "b"}]}]), encoding="utf-8")
    try:
        load_workflows(p)
    except WorkflowError:
        return
    raise AssertionError("accepted duplicate name")


def test_malformed_file_is_a_clean_error(tmp_path):
    p = tmp_path / "w.json"
    p.write_text("{not json", encoding="utf-8")
    try:
        load_workflows(p)
    except (ValueError, WorkflowError):
        return
    raise AssertionError("accepted malformed JSON")


# ---------------------------------------------------------------- runner with fakes
class Recorder:
    def __init__(self, reply_kind="done"):
        self.intents, self.urls, self.folders, self.sleeps = [], [], [], []
        self.reply_kind = reply_kind

    def handler(self, intent, now):
        self.intents.append(intent)
        if self.reply_kind == "confirm":
            return Reply("confirm", "Are you sure?", "x")
        if self.reply_kind == "unavailable":
            return Reply("unavailable", "not here", "x")
        return Reply("done", "ok", "x")

    def runner(self, folders_ok=True):
        return WorkflowRunner(self.handler,
                              open_url=lambda u: (self.urls.append(u), True)[1],
                              open_folder=lambda k: (self.folders.append(k), folders_ok)[1],
                              sleep=lambda s: self.sleeps.append(s))


def wf(*steps):
    return validate_workflow({"name": "T", "steps": list(steps)})


def test_steps_run_in_order_with_per_step_results():
    rec = Recorder()
    res = rec.runner().run(wf({"type": "set_volume", "level": 30}, {"type": "show_tasks", "range": "today"},
                              {"type": "notify", "text": "done"}), NOW)
    assert [s.status for s in res.steps] == ["ok", "ok", "ok"]
    assert [i.name for i in rec.intents] == ["set_volume", "show_tasks"]


def test_timer_creates_a_real_reminder_intent_at_the_right_time():
    rec = Recorder()
    rec.runner().run(wf({"type": "start_timer", "minutes": 25, "label": "Focus block finished"}), NOW)
    i = rec.intents[0]
    assert i.name == "create_reminder" and i.get("title") == "Focus block finished"
    assert i.get("due_at") == NOW + timedelta(minutes=25)


def test_web_search_builds_encoded_https_url_and_nothing_is_sent():
    rec = Recorder()
    rec.runner().run(wf({"type": "web_search", "query": "study tips & focus"}), NOW)
    assert rec.urls == ["https://duckduckgo.com/?q=study+tips+%26+focus"]


def test_confirmation_level_replies_are_refused_inside_workflows():
    rec = Recorder(reply_kind="confirm")
    res = rec.runner().run(wf({"type": "set_volume", "level": 10}), NOW)
    assert res.steps[0].status == "refused" and res.problem_count == 1


def test_music_is_reported_not_yet_available_not_faked():
    rec = Recorder()
    res = rec.runner().run(wf({"type": "play_music", "query": "lo-fi"}), NOW)
    assert res.steps[0].status == "unavailable" and res.ok_count == 0


def test_missing_study_notes_folder_is_an_optional_unavailable_step():
    rec = Recorder()
    res = rec.runner(folders_ok=False).run(wf({"type": "open_folder", "folder": "notes"},
                                              {"type": "notify", "text": "after"}), NOW)
    assert [s.status for s in res.steps] == ["unavailable", "ok"]


def test_pause_is_bounded_per_step_and_in_total():
    rec = Recorder()
    steps = [{"type": "pause", "seconds": 30}] * 3
    res = rec.runner().run(wf(*steps), NOW)
    assert rec.sleeps == [30, 30] and res.steps[2].status == "refused"


def test_cancel_stops_before_the_next_step():
    rec = Recorder()
    token = CancelToken()
    real = rec.handler

    def handler(intent, now):
        token.cancel()           # user cancels during step 1
        return real(intent, now)
    runner = WorkflowRunner(handler, lambda u: True, lambda k: True, lambda s: None)
    res = runner.run(wf({"type": "set_volume", "level": 1}, {"type": "show_tasks", "range": "today"}), NOW, token)
    assert res.cancelled and res.steps[1].status == "skipped" and len(rec.intents) == 1


def test_exception_in_one_step_stops_later_actions():
    rec = Recorder()

    def boom(intent, now):
        raise RuntimeError("bad")
    runner = WorkflowRunner(boom, lambda u: True, lambda k: True, lambda s: None)
    res = runner.run(wf({"type": "set_volume", "level": 1}, {"type": "notify", "text": "still here"}), NOW)
    assert res.stopped and [step.status for step in res.steps] == ["failed", "skipped"]


# ---------------------------------------------------------------- service
def test_run_named_is_case_insensitive_and_lists_unknown_names():
    rec = Recorder()
    svc = WorkflowService(load_workflows(WF_FILE), rec.runner())
    r = svc.run_named("study", NOW)
    assert r.kind in ("done", "info") and "Study workflow" in r.text
    bad_reply = svc.run_named("Nonexistent", NOW)
    assert bad_reply.kind == "error" and "Work" in bad_reply.text


def test_format_result_marks_each_step():
    rec = Recorder()
    res = rec.runner().run(wf({"type": "notify", "text": "hi"}), NOW)
    text = format_result(res)
    assert "1. notify: ok - hi" in text and "finished: 1 ok, 0 problem(s)" in text
