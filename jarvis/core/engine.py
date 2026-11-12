"""The single command entry point: text -> parse -> dialogue -> policy -> action -> Reply.

Pure Python (no Qt). Typed text, speech transcripts, hotkeys, workflows and
phone requests all call `Engine.handle(text, source=...)`.
Approval rules live here: CONFIRM actions wait for a typed/UI confirmation;
voice, workflow, model and phone sources can never confirm.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Callable, Optional

from ..tasks.service import TaskService, describe
from .dialog import DialogManager
from .intents import Intent, Reply
from .parser import EXAMPLES, Parser
from .safety import CONFIRM, REFUSE, TYPED_YES, confirmation_ok, policy_for

_YES = re.compile(r"(?:yes|y|confirm|ok|okay|sure|do\s+it|go\s+ahead)[.!]?", re.I)
_NO = re.compile(r"(?:no|n|cancel|stop|abort|never\s*mind|don'?t)[.!]?", re.I)
TASK_INTENTS = {"create_reminder", "create_task", "show_tasks", "complete_item", "snooze",
                "reschedule", "delete_item", "task_calendar"}


SYSTEM_INTENTS = {"open_app", "close_app", "set_volume", "volume_up", "volume_down",
                  "mute", "unmute", "sysinfo", "power_plan", "power_list"}


class Engine:
    def __init__(self, parser: Parser, tasks: Optional[TaskService] = None, *,
                 confirm_ttl: int = 60, dialog_ttl: int = 120, system=None):
        self.parser, self.tasks = parser, tasks
        self.system = system
        self.workflows = None
        self.dialog = DialogManager(parser, ttl_seconds=dialog_ttl)
        self.confirm_ttl = confirm_ttl
        self.paused = False
        self.handlers = {}
        self._confirm: Optional[tuple] = None     # (action, since, policy)
        self._choice: Optional[tuple] = None      # (intent, [Item], since)

    # ------------------------------------------------------------ controls (UI/tray/orb call these)
    def pause(self) -> None:
        """Pause Jarvix: cancels pending approvals/questions/choices; new commands are refused."""
        self.paused = True
        self.cancel_pending()
        if self.workflows is not None:
            self.workflows.cancel_running()

    def resume(self) -> None:
        self.paused = False

    def cancel_pending(self) -> None:
        self._confirm = self._choice = None
        self.dialog.clear()
        if self.workflows is not None and hasattr(self.workflows, "clear_draft"):
            self.workflows.clear_draft()

    @property
    def awaiting_confirmation(self) -> bool:
        return self._confirm is not None

    def confirm(self, source: str = "ui", now: Optional[datetime] = None, text: str = "") -> Reply:
        """Confirm the pending action (a UI button calls this with source='ui')."""
        now = now or datetime.now()
        if not confirmation_ok(CONFIRM, source):          # source is checked FIRST, always
            return Reply("refused", "Confirmations must be typed or clicked, not spoken or automated.", "confirm.source")
        if self._confirm is None:
            return Reply("info", "Nothing is waiting for confirmation.", "confirm.none")
        action, since, level = self._confirm
        if self.paused or not 0 <= (now - since).total_seconds() <= self.confirm_ttl:
            self._confirm = None
            return Reply("error", "That confirmation expired. Please ask again.", "confirm.expired")
        if level == TYPED_YES and not confirmation_ok(level, source, text):
            return Reply("refused", 'Type the exact word "yes" to apply this plan.', "confirm.typed")
        self._confirm = None
        return action(now)

    # ------------------------------------------------------------ main entry
    def handle(self, text: str, source: str = "typed", now: Optional[datetime] = None) -> Reply:
        now = now or datetime.now()
        plain = (text or "").strip()

        control = self.parser.parse(plain, now) if plain else None
        if control and control.name == "jarvix_pause":
            return self._dispatch(control, now)
        if self.paused:
            if control and control.name == "jarvix_exit":
                return self._dispatch(control, now)
            if control and control.name == "jarvix_resume" and source in {"typed", "ui"}:
                self.paused = False
                return Reply("info", "Jarvix resumed.", "pause.resumed")
            return Reply("info", "Jarvix is paused. Type 'resume jarvix' to continue.", "pause.paused")

        if self._confirm is not None:
            _, since, _ = self._confirm
            if not 0 <= (now - since).total_seconds() <= self.confirm_ttl:
                self._confirm = None
                if _YES.fullmatch(plain):
                    return Reply("error", "That confirmation expired. Please ask again.", "confirm.expired")
            elif _YES.fullmatch(plain):
                return self.confirm(source, now, plain)
            elif _NO.fullmatch(plain):
                self._confirm = None
                return Reply("info", "Cancelled. Nothing was changed.", "confirm.cancelled")
            else:
                self._confirm = None        # a different command makes the old confirmation stale

        if self._choice is not None:
            out = self._answer_choice(plain, now)
            if out is not None:
                return out

        if self.workflows is not None and hasattr(self.workflows, "edit_draft"):
            reply = self.workflows.edit_draft(plain, now)
            if reply is not None:
                return reply

        r = self.dialog.handle(text, now)
        if r.kind == "ask":
            return Reply("ask", r.question, r.question_key, intent=r.intent)
        if r.kind == "cancelled":
            return Reply("info", "Cancelled.", "cancel.done")
        if r.kind == "nothing_to_cancel":
            return Reply("info", "Nothing to cancel.", "cancel.none")
        return self._dispatch(r.intent, now)

    def ui_intent(self, intent: Intent, now: Optional[datetime] = None) -> Reply:
        """Structured commands from explicit controls use the same policies."""
        if self.paused:
            return Reply("refused", "Resume Jarvix to use this control.", "pause.paused")
        self.cancel_pending()
        return self._dispatch(intent, now or datetime.now())

    # ------------------------------------------------------------ dispatch
    def _dispatch(self, intent: Intent, now: datetime) -> Reply:
        n = intent.name
        level = policy_for(n)
        if level == REFUSE and n != "unknown":
            if n == "force_close_refused":
                return Reply("refused", "I don't force-close apps. Ask me to close it normally instead.", "close.force_refused", intent=intent)
            return Reply("refused", "I can't do that.", "refused", intent=intent)
        if n == "unknown":
            return Reply("unknown", "I didn't understand that. Try: " + "; ".join(EXAMPLES[:4]), "unknown", {"suggestions": EXAMPLES}, intent)
        if n == "help":
            return Reply("info", "You can say, for example:\n  " + "\n  ".join(EXAMPLES), "help", intent=intent)
        if n == "jarvix_pause":
            self.pause()
            return Reply("info", "Jarvix paused. Pending approvals were cancelled.", "pause.paused", intent=intent)
        if n == "jarvix_resume":
            return Reply("info", "Jarvix isn't paused.", "pause.not_paused", intent=intent)
        if n == "jarvix_exit":
            return Reply("exit", "Goodbye.", "exit", intent=intent)
        if n in SYSTEM_INTENTS:
            if self.system is None:
                return Reply("unavailable", "System actions are not connected in this build.", "system.none", intent=intent)
            reply, action = self.system.handle(intent, now)
            if action is not None:
                self._confirm = (action, now, level)
            return reply
        if n == "run_workflow":
            if self.workflows is None:
                return Reply("unavailable", "Workflows are not connected in this build.", "workflow.none", intent=intent)
            return self.workflows.run_named(intent.get("workflow") or "", now)
        if n == "create_workflow":
            if self.workflows is not None:
                return self.workflows.begin(intent.get("name", ""), now)
            return Reply("unavailable", "Workflow editing is unavailable.", "workflow.none", intent=intent)
        if n in TASK_INTENTS:
            if self.tasks is None:
                return Reply("error", "Tasks are not available (no database).", "task.nodb", intent=intent)
            return self._tasks(intent, now)
        if n in self.handlers:
            reply, action = self.handlers[n](intent, now)
            if action is not None:
                if level not in {CONFIRM, TYPED_YES} or reply.kind != "confirm":
                    return Reply("refused", "This action cannot request that approval.", "confirm.policy")
                self._confirm = (action, now, level)
            return reply
        return Reply("unavailable", "That action isn't available in this build yet (it arrives in a later checkpoint).",
                     "unavailable", intent=intent)

    def workflow_step(self, intent: Intent, now: datetime) -> Reply:
        """Handler for workflow steps. Anything that would need confirmation is refused."""
        if intent.name in SYSTEM_INTENTS:
            if self.system is None:
                return Reply("unavailable", "System actions are not connected.", "system.none", intent=intent)
            reply, action = self.system.handle(intent, now)
            if action is not None:
                return Reply("refused", "Workflows can't make this kind of change.", "workflow.refused", intent=intent)
            return reply
        if intent.name in TASK_INTENTS and self.tasks is not None:
            return self._tasks(intent, now)
        return Reply("unavailable", "Not available inside workflows.", "workflow.unavailable", intent=intent)

    _workflow_step = workflow_step  # backward-compatible alias

    def _tasks(self, intent: Intent, now: datetime) -> Reply:
        t, s, n = self.tasks, intent.slots, intent.name
        if n in ("create_reminder", "create_task"):
            return t.create(intent, now)
        if n == "show_tasks":
            return t.show(s.get("range", "today"), now)
        if n == "task_calendar":
            return t.calendar(s.get("day"), now)
        if n == "complete_item":
            return t.complete(s["id"], now)
        if n == "reschedule":
            due = s.get("due_at")
            return t.reschedule(s["id"], due, now) if due else Reply("error", "I need a new time.", "task.error")
        if n == "delete_item":
            it, err = t.delete_preview(s["id"], s.get("kind"))
            if err:
                return err
            def action(now2, _id=it.id):
                return t.delete(_id, now2)
            self._confirm = (action, now, CONFIRM)
            return Reply("confirm", f"Delete this {it.type}?\n  {describe(it, now)}\nType 'yes' (or press Confirm) to delete, or 'no' to keep it.",
                         "delete.confirm", {"id": it.id}, intent)
        if n == "snooze":
            return self._snooze(intent, now)
        return Reply("error", "Unsupported task action.", "task.error")

    def _snooze(self, intent: Intent, now: datetime) -> Reply:
        s = intent.slots
        id_ = s.get("id")
        if id_ is None:
            cands = self.tasks.snooze_candidates(now)
            if not cands:
                return Reply("error", "I don't see a reminder that has gone off. Say 'snooze reminder <number> for 10 minutes'.", "snooze.none", intent=intent)
            if len(cands) > 1:
                self._choice = (intent, cands, now)
                opts = "\n".join(f"  {describe(c, now)}" for c in cands)
                return Reply("ask", f"Which reminder?\n{opts}\nSay the number.", "ask.snooze_which", {"ids": [c.id for c in cands]}, intent)
            id_ = cands[0].id
        return self.tasks.snooze(id_, now, s.get("minutes"), s.get("until"))

    def _answer_choice(self, plain: str, now: datetime) -> Optional[Reply]:
        intent, cands, since = self._choice
        if (now - since).total_seconds() > self.dialog.ttl:
            self._choice = None
            return None
        if _NO.fullmatch(plain):
            self._choice = None
            return Reply("info", "Cancelled.", "cancel.done")
        m = re.fullmatch(r"(?:#|number\s+|no\.?\s*)?(\d+)\.?", plain, re.I)
        if m and int(m.group(1)) in {c.id for c in cands}:
            self._choice = None
            return self.tasks.snooze(int(m.group(1)), now, intent.slots.get("minutes"), intent.slots.get("until"))
        if self.parser.parse(plain, now).name not in ("unknown", "cancel"):
            self._choice = None
            return None          # a different command: drop the question
        return Reply("ask", "Please say one of the numbers: " + ", ".join(f"#{c.id}" for c in cands), "ask.snooze_which")
