"""Validate and load workflows. Every step is checked before anything runs.

Steps can only use the fixed types below. Workflows cannot close apps, change
power plans, delete items, or send messages (confirmation-level actions are
never allowed inside a workflow). Folder steps use fixed keys, never raw paths.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

from ..config.settings import read_json

MAX_STEPS = 10
MAX_NAME = 40
MAX_TEXT = 200
MAX_PAUSE_SECONDS = 30
MAX_TIMER_MINUTES = 480
FOLDER_KEYS = ("desktop", "documents", "downloads", "notes")
TASK_RANGES = ("today", "tomorrow", "week")
URL_SCHEMES = ("https", "http")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

STEP_FIELDS = {
    "open_app": {"app": str},
    "open_url": {"url": str},
    "web_search": {"query": str},
    "play_music": {"query": str},
    "speak": {"text": str},
    "set_volume": {"level": int},
    "show_tasks": {"range": str},
    "start_timer": {"minutes": int, "label": str},
    "pause": {"seconds": int},
    "notify": {"text": str},
    "open_folder": {"folder": str},
    "open_study_page": {},
}


class WorkflowError(ValueError):
    pass


def _text(value, field, max_len, min_len=1):
    if not isinstance(value, str):
        raise WorkflowError(f"'{field}' must be text")
    v = value.strip()
    if not min_len <= len(v) <= max_len:
        raise WorkflowError(f"'{field}' must be {min_len}-{max_len} characters")
    if _CONTROL.search(v):
        raise WorkflowError(f"'{field}' contains control characters")
    return v


def _int(value, field, lo, hi):
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkflowError(f"'{field}' must be a whole number")
    if not lo <= value <= hi:
        raise WorkflowError(f"'{field}' must be between {lo} and {hi}")
    return value


def validate_url(url: str) -> str:
    u = _text(url, "url", 2000)
    if "\\" in u or any(ch.isspace() for ch in u):
        raise WorkflowError("'url' must not contain spaces")
    parts = urlsplit(u)
    if (parts.scheme.lower() not in URL_SCHEMES or not parts.hostname or
            parts.username is not None or parts.password is not None or "%" in parts.netloc):
        raise WorkflowError("'url' must be an http or https address")
    _ = parts.port   # raises on malformed/out-of-range ports
    return u


def validate_step(step, index: int) -> dict:
    if not isinstance(step, dict):
        raise WorkflowError(f"step {index}: must be an object")
    kind = step.get("type")
    if kind not in STEP_FIELDS:
        raise WorkflowError(f"step {index}: unknown step type {kind!r}")
    allowed = STEP_FIELDS[kind]
    extra = set(step) - set(allowed) - {"type"}
    if extra:
        raise WorkflowError(f"step {index}: unexpected fields {sorted(extra)}")
    out = {"type": kind}
    try:
        if kind in ("open_app",):
            out["app"] = _text(step.get("app"), "app", 40)
        elif kind == "open_url":
            out["url"] = validate_url(step.get("url"))
        elif kind == "web_search":
            out["query"] = _text(step.get("query"), "query", 100, 2)
        elif kind == "play_music":
            out["query"] = _text(step.get("query"), "query", 100, 1)
        elif kind == "speak":
            out["text"] = _text(step.get("text"), "text", MAX_TEXT)
        elif kind == "set_volume":
            out["level"] = _int(step.get("level"), "level", 0, 100)
        elif kind == "show_tasks":
            if step.get("range") not in TASK_RANGES:
                raise WorkflowError(f"'range' must be one of {', '.join(TASK_RANGES)}")
            out["range"] = step["range"]
        elif kind == "start_timer":
            out["minutes"] = _int(step.get("minutes"), "minutes", 1, MAX_TIMER_MINUTES)
            out["label"] = _text(step.get("label", "Timer finished"), "label", 100)
        elif kind == "pause":
            out["seconds"] = _int(step.get("seconds"), "seconds", 1, MAX_PAUSE_SECONDS)
        elif kind == "notify":
            out["text"] = _text(step.get("text"), "text", MAX_TEXT)
        elif kind == "open_folder":
            if step.get("folder") not in FOLDER_KEYS:
                raise WorkflowError(f"'folder' must be one of {', '.join(FOLDER_KEYS)}")
            out["folder"] = step["folder"]
    except WorkflowError as exc:
        raise WorkflowError(f"step {index}: {exc}") from None
    return out


def validate_workflow(wf) -> dict:
    if not isinstance(wf, dict):
        raise WorkflowError("workflow must be an object")
    name = _text(wf.get("name"), "name", MAX_NAME)
    steps = wf.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise WorkflowError(f"'{name}': steps must be a list of 1 to {MAX_STEPS} items")
    return {"name": name, "steps": [validate_step(s, i + 1) for i, s in enumerate(steps)]}


def load_workflows(path) -> dict:
    """Return {name: workflow}. Raises WorkflowError on any invalid or duplicate entry."""
    raw = read_json(Path(path))
    if not isinstance(raw, list) or len(raw) > 64:
        raise WorkflowError("workflows file must be a list of at most 64 workflows")
    out = {}
    for wf in raw:
        v = validate_workflow(wf)
        key = v["name"].casefold()
        if key in out:
            raise WorkflowError(f"duplicate workflow name: {v['name']}")
        out[key] = v
    return out
