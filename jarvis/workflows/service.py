"""Named workflow runs for the command engine (`run_named`)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..config.settings import write_json
from ..core.intents import Reply
from .loader import MAX_NAME, MAX_STEPS, STEP_FIELDS, load_workflows, validate_step, validate_workflow
from .runner import RunResult, WorkflowRunner

STATUS_MARK = {"ok": "ok", "failed": "FAILED", "refused": "REFUSED", "unavailable": "unavailable", "skipped": "skipped"}


def format_result(res: RunResult) -> str:
    lines = [f"{res.name} workflow:"]
    for s in res.steps:
        lines.append(f"  {s.index}. {s.kind}: {STATUS_MARK[s.status]} - {s.detail}")
    tail = "cancelled" if res.cancelled else "stopped" if res.stopped else "finished"
    lines.append(f"{tail}: {res.ok_count} ok, {res.problem_count} problem(s)")
    return "\n".join(lines)


class WorkflowService:
    def __init__(self, workflows: dict, runner: WorkflowRunner, path=None, catalog=None):
        self.workflows, self.runner = dict(workflows), runner
        self._cancel = None
        self._draft = None
        self.path = Path(path) if path is not None else None
        self.catalog = catalog
        self.problem = ""
        if self.path is not None:
            try:
                self.workflows = load_workflows(self.path)
            except FileNotFoundError:
                pass
            except (OSError, ValueError):
                self.problem = "Custom workflows could not be loaded. Starter workflows are active."
        self._refresh_catalog()

    def _refresh_catalog(self):
        if self.catalog is not None:
            self.catalog.workflows = self.names()
            self.catalog.__post_init__()

    def names(self) -> list:
        return sorted(w["name"] for w in self.workflows.values())

    def run_named(self, name: str, now: datetime) -> Reply:
        wf = self.workflows.get((name or "").strip().casefold())
        if wf is None:
            return Reply("error", f"I don't have a workflow called '{name}'. Available: {', '.join(self.names())}.",
                         "workflow.unknown")
        from .runner import CancelToken
        token = self._cancel = CancelToken()
        try:
            res = self.runner.run(wf, now, token)
        finally:
            self._cancel = None
        kind = "done" if res.problem_count == 0 and not res.cancelled else "info"
        return Reply(kind, format_result(res), "workflow.result", {"name": res.name}, None)

    def cancel_running(self) -> bool:
        token = self._cancel
        if token is None:
            return False
        token.cancel()
        return True

    def save(self, old_name, workflow):
        value = validate_workflow(workflow)
        old_key = old_name.casefold()
        new_key = value["name"].casefold()
        if old_key and old_key not in self.workflows:
            raise ValueError("The workflow was renamed. Reload the editor.")
        if new_key in self.workflows and old_key != new_key:
            raise ValueError("That workflow name is already in use.")
        values = {key: item for key, item in self.workflows.items() if key != old_key}
        if len(values) >= 64:
            raise ValueError("The workflow list already contains 64 entries.")
        values[new_key] = value
        if self.path is not None:
            write_json(self.path, list(values.values()))
        self.workflows = values
        self._refresh_catalog()
        return Reply("done", f"Saved the {value['name']} workflow.", "workflow.saved",
                     {"name": value["name"]})

    def handle_editor(self, intent, _now):
        try:
            return self.save(intent.get("original", ""), intent.get("workflow")), None
        except (OSError, ValueError) as exc:
            return Reply("error", str(exc), "workflow.editor"), None

    def begin(self, name, now):
        if not 1 <= len(name) <= MAX_NAME or name.casefold() in self.workflows:
            return Reply("error", "Choose a new workflow name up to 40 characters.", "workflow.editor")
        self._draft = (name, [], now)
        return Reply("ask", f"Add a step for {name}. Try 'add open_app Notepad' or 'add start_timer 25 Study'. "
                     "Type 'save workflow' when done.", "workflow.draft")

    def clear_draft(self):
        self._draft = None

    def edit_draft(self, text, now):
        if self._draft is None:
            return None
        name, steps, since = self._draft
        if not 0 <= (now - since).total_seconds() <= 180:
            self.clear_draft()
            return None
        text = text.strip()
        command = text.casefold()
        try:
            if command == "cancel":
                self.clear_draft()
                return Reply("info", "Discarded the workflow draft.", "workflow.editor")
            if command == "save workflow":
                reply = self.save("", {"name": name, "steps": steps})
                self.clear_draft()
                return reply
            if not command.startswith("add "):
                return Reply("ask", "Add a step, save workflow, or cancel.", "workflow.draft")
            if len(steps) >= MAX_STEPS:
                raise ValueError(f"Workflows can have up to {MAX_STEPS} steps.")
            parts = text.split(maxsplit=2)
            kind = parts[1].casefold()
            fields = STEP_FIELDS.get(kind)
            if fields is None:
                raise ValueError("Use one of these steps: " + ", ".join(STEP_FIELDS))
            arguments = parts[2].split(maxsplit=len(fields) - 1) if len(parts) == 3 and fields else []
            if len(arguments) != len(fields):
                raise ValueError(f"Use add {kind} " + " ".join(fields))
            step = {"type": kind}
            for (key, type_), value in zip(fields.items(), arguments):
                step[key] = type_(value)
            steps.append(validate_step(step, len(steps) + 1))
            self._draft = (name, steps, now)
            return Reply("ask", f"Step {len(steps)} added. Add another step or type 'save workflow'.",
                         "workflow.draft")
        except (OSError, ValueError) as exc:
            return Reply("error", str(exc), "workflow.editor")
