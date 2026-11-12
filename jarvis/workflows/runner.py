"""Run a validated workflow step by step. Pure logic; every effect is injected.

  handler(intent, now) -> Reply     engine/system/tasks adapter (confirmation replies are refused)
  open_url(url) -> bool             default browser (webbrowser in the real app)
  open_folder(key) -> bool          fixed folder keys only
  sleep(seconds)                    injected so tests don't wait
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import Event
from typing import Callable, Optional
from urllib.parse import quote_plus

from ..core.intents import Intent, Reply
from .loader import MAX_PAUSE_SECONDS, validate_url

MAX_TOTAL_PAUSE = 60
SEARCH_URL = "https://duckduckgo.com/?q={}"


@dataclass(frozen=True)
class StepResult:
    index: int
    kind: str
    status: str        # ok | failed | refused | unavailable | skipped
    detail: str


@dataclass
class RunResult:
    name: str
    steps: list = field(default_factory=list)
    cancelled: bool = False
    stopped: bool = False

    @property
    def ok_count(self) -> int:
        return sum(1 for s in self.steps if s.status == "ok")

    @property
    def problem_count(self) -> int:
        return sum(1 for s in self.steps if s.status in ("failed", "refused"))


class CancelToken:
    def __init__(self):
        self._e = Event()

    def cancel(self) -> None:
        self._e.set()

    @property
    def cancelled(self) -> bool:
        return self._e.is_set()

    def wait(self, seconds: float) -> bool:
        return self._e.wait(seconds)


class WorkflowRunner:
    def __init__(self, handler: Callable, open_url: Callable[[str], bool],
                 open_folder: Callable[[str], bool], sleep: Callable[[float], None] = time.sleep,
                 *, play=None, speak=None, notify=None, study_url=None, on_step=None):
        self.handler, self.open_url, self.open_folder, self.sleep = handler, open_url, open_folder, sleep
        self.play, self.speak, self.notify = play, speak, notify
        self.study_url, self.on_step = study_url, on_step

    def run(self, wf: dict, now: datetime, cancel: Optional[CancelToken] = None) -> RunResult:
        cancel = cancel or CancelToken()
        result = RunResult(wf["name"])
        paused_total = 0
        for i, step in enumerate(wf["steps"], start=1):
            if cancel.cancelled:
                result.cancelled = True
                result.steps.append(StepResult(i, step["type"], "skipped", "cancelled"))
                continue
            if result.stopped:
                result.steps.append(StepResult(i, step["type"], "skipped", "an earlier step failed"))
                continue
            kind = step["type"]
            try:
                if kind == "pause":
                    if paused_total + step["seconds"] > MAX_TOTAL_PAUSE:
                        status, detail = "refused", f"total pause would exceed {MAX_TOTAL_PAUSE} seconds"
                    else:
                        paused_total += step["seconds"]
                        seconds = min(step["seconds"], MAX_PAUSE_SECONDS)
                        if self.sleep is time.sleep and cancel.wait(seconds):
                            result.cancelled = True
                            status, detail = "skipped", "cancelled during wait"
                        else:
                            if self.sleep is not time.sleep:
                                self.sleep(seconds)
                            status, detail = "ok", f"waited {seconds}s"
                else:
                    status, detail = self._do(step, now)
            except Exception as exc:
                status, detail = "failed", f"{type(exc).__name__}"
            item = StepResult(i, kind, status, detail)
            result.steps.append(item)
            if self.on_step is not None:
                self.on_step(item)
            result.stopped = status in {"failed", "refused"}
        return result

    def _do(self, step: dict, now: datetime):
        kind = step["type"]
        if kind == "open_url":
            return self._url(step["url"])
        if kind == "open_study_page":
            url = self.study_url() if self.study_url else ""
            return self._url(url) if url else ("unavailable", "Set a study page in Settings.")
        if kind == "web_search":
            return self._url(SEARCH_URL.format(quote_plus(step["query"])))
        if kind == "open_folder":
            if step["folder"] not in ("desktop", "documents", "downloads", "notes"):
                return "refused", "unknown folder"
            if self.open_folder(step["folder"]):
                return "ok", f"opened {step['folder']}"
            return ("unavailable" if step["folder"] == "notes" else "failed",
                    f"{step['folder']} folder is missing or not approved")
        if kind == "play_music":
            return self.play(step["query"]) if self.play else ("unavailable", "Local player unavailable.")
        if kind == "speak":
            return self.speak(step["text"]) if self.speak else ("unavailable", "Speech output is off.")
        if kind == "notify":
            if self.notify:
                self.notify(step["text"])
            return "ok", step["text"]
        if kind == "start_timer":
            due = now + timedelta(minutes=step["minutes"])
            intent = Intent("create_reminder", {"kind": "reminder", "title": step["label"], "due_at": due})
            return self._reply(intent, now, f"timer set for {step['minutes']} min")
        if kind == "open_app":
            return self._reply(Intent("open_app", {"app": step["app"]}), now, f"opened {step['app']}")
        if kind == "set_volume":
            return self._reply(Intent("set_volume", {"level": step["level"]}), now, f"volume {step['level']}%")
        if kind == "show_tasks":
            return self._reply(Intent("show_tasks", {"range": step["range"]}), now, f"showed {step['range']} tasks")
        return "refused", "unsupported step"

    def _url(self, url: str):
        validate_url(url)
        return ("ok", "opened in browser") if self.open_url(url) else ("failed", "browser did not open")

    def _reply(self, intent: Intent, now: datetime, ok_detail: str):
        r: Reply = self.handler(intent, now)
        if r.kind in ("confirm", "refused"):
            return "refused", "needs confirmation, so it can't run inside a workflow"
        if r.kind == "done":
            return "ok", ok_detail
        if r.kind == "info" and intent.name == "show_tasks":
            return "ok", r.text
        if r.kind == "unavailable":
            return "unavailable", r.text
        return "failed", r.text
