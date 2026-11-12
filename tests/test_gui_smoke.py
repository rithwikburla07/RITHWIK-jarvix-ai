"""GUI smoke tests. They need PySide6 and run offscreen (no visible window).
Skipped automatically when PySide6 is not installed."""
import os
import queue

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from jarvis.core.intents import Reply  # noqa: E402
from jarvis.ui.app import NAV, Bridge, CommandWorker, MainWindow  # noqa: E402

NAMES = ["Work", "Focus", "Study", "Research", "Planning", "Creative",
         "Morning", "Email", "Meeting", "Coding", "Music", "Finance"]


class FakeEngine:
    paused = False
    workflows = None

    def handle(self, text, source="typed"):
        return Reply("done", f"echo {text}", "x")

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False


def window():
    app = QApplication.instance() or QApplication([])
    bridge = Bridge()
    worker = CommandWorker(FakeEngine(), bridge)     # not started: we only inspect its queue
    win = MainWindow(FakeEngine(), bridge, worker, NAMES)
    return win, worker


def test_navigation_has_all_thirteen_entries_and_pages_match():
    win, _ = window()
    assert win.nav.count() == len(NAV) == 13
    assert win.pages.count() == 13


def test_twelve_workflow_quick_buttons_exist():
    win, _ = window()
    assert [b.text() for b in win.workflow_buttons] == NAMES


def test_typed_command_is_queued_and_logged():
    win, worker = window()
    win.input.setText("Open Notepad")
    win.send_from_box()
    assert worker.inbox.get_nowait() == ("Open Notepad", "typed")
    assert "you> Open Notepad" in win.log.toPlainText()


def test_reply_slot_logs_and_sets_status():
    win, _ = window()
    win.on_reply("done", "Opening Notepad.", "Open Notepad")
    assert "jarvix> Opening Notepad." in win.log.toPlainText()
    assert win.status.text() == "Done"


def test_history_up_arrow_recalls_previous_command():
    win, _ = window()
    win.send_text("volume 30")
    win.send_text("what's my battery")
    win._history_step(-1)
    assert win.input.text() == "what's my battery"
    win._history_step(-1)
    assert win.input.text() == "volume 30"


def test_pause_button_queues_pause_and_label_follows_engine_state():
    win, worker = window()
    win.toggle_pause()
    assert worker.inbox.get_nowait() == ("pause jarvix", "typed")
    assert win.pause_btn.text() == "Pause Jarvix"    # engine not actually paused in this fake
