"""PySide6 desktop window with a shared background command worker."""
from __future__ import annotations

import queue
import sys
from datetime import datetime
from typing import Callable, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout, QFrame,
                               QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
                               QSpinBox, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget)

from .. import __version__
from ..config.settings import Settings
from ..core.intents import Intent
from ..integrations.brain import endpoint as brain_endpoint
from ..integrations.diagnostics import write_report
from ..logging_setup import LOG, record
from ..core.safety import PathEnv
from ..system.desktop import local_directory
from ..i18n.locale import LANGUAGES, Locale
from ..core.redact import redact_text
from .hud import Dashboard, DashboardPoller, FloatingOrb, reactor_icon
from ..voice.backends import dependency_status
from .voice_controls import VoiceControls
from .integration_panels import GmailPanel, MusicPanel, WhatsAppPanel
from .task_panel import TaskPanel
from .phone_panel import PhonePanel
from .battle import BattlePanel
from .backdrop import Backdrop
from .hotkey import CHORDS, Hotkey
from .model_dialog import ModelDialog
from .workflow_editor import WorkflowEditor

NAV = ["Home", "Tasks", "Workflows", "Apps", "WhatsApp", "Gmail", "Android", "Music",
       "Battle Mode", "Settings", "Diagnostics", "History", "About"]
STATUS = {
    "done": "Done", "info": "Ready", "ask": "Waiting for your answer",
    "confirm": "Type yes or no to confirm", "refused": "Refused", "unknown": "Didn't understand",
    "unavailable": "Not available yet", "error": "Error", "exit": "Exiting", "working": "Working...",
}
STYLE = """
QMainWindow, QWidget { background: #0b1118; color: #d9e6ef; font-family: 'Segoe UI', sans-serif; font-size: 13px; }
QFrame#panel { background: #101b24; border: 1px solid #244253; border-radius: 10px; }
QFrame#meter { background: #0e151d; border: 1px solid #223747; border-radius: 6px; }
QFrame#panel QWidget, QFrame#meter QWidget { background: transparent; }
QListWidget { background: #0e1821; border: 1px solid #243b4a; border-radius: 9px; padding: 6px; outline: 0; }
QListWidget::item { padding: 8px 9px; border-radius: 4px; }
QListWidget::item:selected { background: #163445; color: #7ff3ff; }
QListWidget::item:focus { border: 1px solid #ffc857; }
QPlainTextEdit, QLineEdit, QComboBox, QSpinBox { background: #080e14; border: 1px solid #2a4658;
    border-radius: 6px; padding: 6px; selection-background-color: #215466; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border-color: #7ff3ff; }
QPushButton { background: #112331; border: 1px solid #325b6e; padding: 6px 11px; color: #d9eaf2; border-radius: 6px; }
QPushButton:hover { background: #1a3846; border-color: #7ff3ff; }
QPushButton:focus { border-color: #ffc857; }
QPushButton:disabled { color: #72848c; border-color: #1f3a48; }
QPushButton#primary { background: #7ff3ff; color: #07141d; border-color: #7ff3ff; font-weight: bold; }
QPushButton#pause { color: #ffc857; border-color: #866b38; }
QLabel#title { color: #7ff3ff; font-size: 24px; font-weight: 700; }
QLabel#status { color: #ffc857; font-weight: 600; }
QLabel#caption { color: #d1e4ee; font-weight: 600; }
QLabel#metric { color: #7ff3ff; font-size: 16px; font-weight: 700; }
QLabel#note { color: #9db1bd; }
QProgressBar { background: #283e4d; border: 0; border-radius: 2px; }
QProgressBar::chunk { background: #7ff3ff; border-radius: 2px; }
QScrollArea { border: 0; }
"""


class Bridge(QObject):
    """Thread-safe signals from the worker and the scheduler into the window."""
    reply = Signal(str, str, str)   # kind, text, command that produced it
    alert = Signal(str)             # reminder alert text
    status = Signal(str)            # short status key, e.g. "working"
    result = Signal(object)
    dashboard = Signal(object)
    workflow_step = Signal(object)
    music = Signal(object)
    notification = Signal(str)
    speak = Signal(str)


class CommandWorker(QThread):
    """Runs engine.handle() one command at a time, off the UI thread."""

    def __init__(self, engine, bridge: Bridge):
        super().__init__()
        self.engine, self.bridge = engine, bridge
        self.inbox: "queue.Queue" = queue.Queue(maxsize=32)

    def submit(self, text: str | Intent, source: str = "typed") -> bool:
        try:
            self.inbox.put_nowait((text, source))
        except queue.Full:
            return False
        return True

    def discard_queued(self) -> None:
        while True:
            try:
                self.inbox.get_nowait()
            except queue.Empty:
                return

    def stop(self, timeout_ms: int = 8000) -> bool:
        """Request cooperative exit; returns False if a command is still running."""
        self.discard_queued()
        self.inbox.put_nowait(None)
        return bool(self.wait(timeout_ms))

    def run(self) -> None:
        while True:
            item = self.inbox.get()
            if item is None:
                return
            text, source = item
            label = text if isinstance(text, str) else text.name
            self.bridge.status.emit("working")
            try:
                reply = (self.engine.handle(text, source=source) if isinstance(text, str)
                         else self.engine.ui_intent(text))
                parser = getattr(self.engine, "parser", None)
                if parser is not None:
                    reply = parser.locale.response(reply)
                record(reply)
                self.bridge.result.emit(reply)
                self.bridge.reply.emit(reply.kind, reply.text, label)
            except Exception as exc:  # the window must survive any single command
                LOG.error("command failed: %s", type(exc).__name__)
                self.bridge.reply.emit("error", f"Internal error ({type(exc).__name__}). Jarvix is still running.", label)


class HistoryLine(QLineEdit):
    """Command bar with Up/Down history."""

    def __init__(self, on_key: Callable[[int], None], on_submit: Callable[[], None]):
        super().__init__()
        self._on_key = on_key
        self.returnPressed.connect(on_submit)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Up, Qt.Key_Down):
            self._on_key(-1 if e.key() == Qt.Key_Up else 1)
            return
        super().keyPressEvent(e)


class MainWindow(QMainWindow):
    def __init__(self, engine, bridge: Bridge, worker: CommandWorker, workflow_names: list):
        super().__init__()
        self.engine, self.bridge, self.worker = engine, bridge, worker
        self.settings = getattr(engine, "settings", None) or Settings()
        self.locale = Locale(self.settings.get("language"))
        self.history: list = []
        self.hist_pos: Optional[int] = None
        self.exiting = False
        self.transcribed = False
        self.preview_visible = False
        self.voice_state = "Idle"
        self.voice = VoiceControls(self.settings, self)
        self.setWindowTitle(f"{self.settings.get('title')} v{__version__}")
        self.setWindowIcon(reactor_icon())
        self.setMinimumSize(900, 590)
        self.resize(1170, 690)
        self.state_timer = QTimer(self)
        self.state_timer.setSingleShot(True)
        self.state_timer.timeout.connect(self.reset_reactor)
        self.tray = None
        self.orb = FloatingOrb()
        self.orb.restore.connect(self.restore)
        self.orb.toggle_pause.connect(self.toggle_pause)
        self.orb.speak.connect(self.push_to_talk)
        self.orb.position_saved.connect(lambda x, y: self.save_settings(orb_x=x, orb_y=y))
        self.poller = DashboardPoller(self, engine, bridge.dashboard)

        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)

        self.nav = QListWidget()
        self.nav.setFixedWidth(154)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav.addItems(NAV)
        self.pages = QStackedWidget()
        outer.addWidget(self.nav)
        outer.addWidget(self.pages, 1)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)

        self.pages.addWidget(self._home_page(workflow_names))
        self.pages.addWidget(self._tasks_page())
        self.pages.addWidget(self._workflows_page(workflow_names))
        self.pages.addWidget(self._apps_page())
        self.whatsapp_panel = WhatsAppPanel(getattr(engine, "whatsapp", None), self.settings,
                                            self.save_settings, self.send_intent)
        self.gmail_panel = GmailPanel(getattr(engine, "gmail", None), self.send_intent)
        self.music_panel = MusicPanel(self.settings, self.save_settings, self.send_intent)
        self.pages.addWidget(self.whatsapp_panel)
        self.pages.addWidget(self.gmail_panel)
        self.phone_panel = PhonePanel(getattr(engine, "phone", None), self.send_intent)
        self.battle_panel = BattlePanel()
        self.pages.addWidget(self.phone_panel)
        self.pages.addWidget(self.music_panel)
        self.pages.addWidget(self.battle_panel)
        self.pages.addWidget(self._settings_page())
        self.pages.addWidget(self._diagnostics_page())
        self.pages.addWidget(self._history_page())
        self.pages.addWidget(self._about_page())
        self.nav.setCurrentRow(0)

        bridge.reply.connect(self.on_reply)
        bridge.alert.connect(self.on_alert)
        bridge.status.connect(self.on_status)
        bridge.result.connect(self.on_result)
        bridge.dashboard.connect(self.on_dashboard)
        bridge.workflow_step.connect(self.workflow_editor.show_step)
        bridge.notification.connect(self.on_alert)
        bridge.speak.connect(self.voice.say)
        bridge.music.connect(self.music_panel.show_reply)
        self.voice.info.connect(self.voice_info)
        self.voice.text_ready.connect(self.voice_transcript)
        self.voice.state.connect(self.on_voice_state)
        self.voice.level.connect(self.dashboard.reactor.set_level)
        self.hotkey = QShortcut(QKeySequence(self.settings.get("hotkey")), self)
        self.hotkey.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self.hotkey.activated.connect(self.push_to_talk)
        self.native_hotkey = None
        self.internet_status = "Internet check available on request."
        self.backdrop.status.connect(lambda message: self.log_line("jarvix", message))
        self.apply_appearance()
        self.apply_language()

    # ------------------------------------------------------------ pages
    def _home_page(self, names) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(10, 2, 2, 2)
        lay.setSpacing(9)
        top = QHBoxLayout()
        self.title = QLabel(self.settings.get("title"))
        self.title.setObjectName("title")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.status = QLabel("Ready")
        self.status.setObjectName("status")
        self.pause_btn = QPushButton("Pause Jarvix")
        self.pause_btn.setObjectName("pause")
        self.pause_btn.clicked.connect(self.toggle_pause)
        top.addWidget(self.title)
        top.addStretch(1)
        top.addWidget(self.status)
        top.addWidget(self.pause_btn)
        lay.addLayout(top)

        self.dashboard = Dashboard()
        self.backdrop = Backdrop(self.dashboard)
        lay.addWidget(self.dashboard)
        details = QHBoxLayout()
        task_card, self.today = self._summary("Today's tasks", "No tasks due today")
        reminder_card, self.upcoming = self._summary("Upcoming reminders", "No upcoming reminders")
        details.addWidget(task_card, 1)
        details.addWidget(reminder_card, 1)
        lay.addLayout(details)

        log_title = QLabel("Command log")
        log_title.setObjectName("caption")
        lay.addWidget(log_title)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setAccessibleName("Command log")
        self.log.setMaximumBlockCount(400)
        lay.addWidget(self.log, 1)

        row = QHBoxLayout()
        self.input = HistoryLine(self._history_step, self.send_from_box)
        self.input.setMaxLength(2000)
        self.input.textEdited.connect(lambda text: self.clear_transcript() if not text else None)
        self.input.setAccessibleName("Command")
        self.input.setPlaceholderText("Type a command, for example Open Notepad")
        send = QPushButton("Send")
        send.setObjectName("primary")
        send.clicked.connect(self.send_from_box)
        self.mic = QPushButton("Push to talk")
        self.mic.clicked.connect(self.push_to_talk)
        row.addWidget(self.input, 1)
        row.addWidget(self.mic)
        row.addWidget(send)
        lay.addLayout(row)

        quick = QGridLayout()
        self.workflow_buttons = []
        for i, name in enumerate(names):
            b = QPushButton(name)
            b.clicked.connect(lambda _=False, button=b: self.send_text(f"run {button.text()} workflow"))
            self.workflow_buttons.append(b)
            quick.addWidget(b, i // 6, i % 6)
        lay.addLayout(quick)

        bottom = QHBoxLayout()
        cancel = QPushButton("Stop workflow")
        cancel.clicked.connect(self.cancel_workflow)
        clear = QPushButton("Clear log view")
        clear.clicked.connect(self.log.clear)
        self.notification = QLabel("No recent reminders")
        self.notification.setObjectName("note")
        self.notification.setTextFormat(Qt.TextFormat.PlainText)
        self.notification.setWordWrap(True)
        self.notification.setMaximumHeight(36)
        bottom.addWidget(cancel)
        bottom.addWidget(clear)
        bottom.addWidget(self.notification, 1)
        lay.addLayout(bottom)
        self.input.setFocus()
        return page

    def _summary(self, heading, empty):
        panel = QFrame()
        panel.setObjectName("panel")
        box = QVBoxLayout(panel)
        box.setContentsMargins(10, 6, 10, 6)
        title = QLabel(heading)
        title.setObjectName("caption")
        label = QLabel(empty)
        label.setObjectName("note")
        label.setTextFormat(Qt.TextFormat.PlainText)
        box.addWidget(title)
        box.addWidget(label)
        return panel, label

    def _tasks_page(self) -> QWidget:
        self.task_panel = TaskPanel(self.send_intent)
        return self.task_panel

    def _workflows_page(self, _names) -> QWidget:
        self.workflow_editor = WorkflowEditor(getattr(self.engine, "workflows", None),
                                              self.send_intent, self.cancel_workflow)
        return self.workflow_editor

    def _apps_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        heading = QLabel("Apps and search")
        heading.setObjectName("title")
        lay.addWidget(heading)
        search_row = QHBoxLayout()
        query = QLineEdit()
        query.setMaxLength(100)
        query.setPlaceholderText("Search approved apps and folders")
        category = QComboBox()
        for label, kind in (("All", "any"), ("Files", "file"), ("Folders", "folder"),
                            ("Applications", "app"), ("Music", "music"), ("Shortcuts", "shortcut")):
            category.addItem(label, kind)
        search_button = QPushButton("Search")
        do_search = lambda: self.send_intent("file_search", query=query.text(), kind=category.currentData())
        search_button.clicked.connect(do_search)
        query.returnPressed.connect(do_search)
        search_row.addWidget(query, 1)
        search_row.addWidget(category)
        search_row.addWidget(search_button)
        lay.addLayout(search_row)
        self.search_results = QListWidget()
        self.search_results.setAccessibleName("Search results")
        lay.addWidget(self.search_results, 1)
        reveal = QPushButton("Open selected app or location")
        reveal.clicked.connect(lambda: self.send_intent("reveal_search_hit", index=self.search_results.currentRow()))
        lay.addWidget(reveal)
        section = QLabel("Approved apps")
        section.setObjectName("caption")
        lay.addWidget(section)
        approved_row = QHBoxLayout()
        self.approved_apps = QComboBox()
        alias = QLineEdit()
        alias.setMaxLength(48)
        alias.setPlaceholderText("Alias, leave blank to clear")
        open_app = QPushButton("Open")
        open_app.clicked.connect(lambda: self.send_intent("open_app", app=self.approved_apps.currentText()))
        save_alias = QPushButton("Save alias")
        save_alias.clicked.connect(lambda: self.send_intent("app_alias", name=self.approved_apps.currentText(),
                                                           alias=alias.text()))
        approved_row.addWidget(self.approved_apps, 1)
        approved_row.addWidget(open_app)
        approved_row.addWidget(alias, 1)
        approved_row.addWidget(save_alias)
        lay.addLayout(approved_row)
        self.refresh_apps()
        scan_row = QHBoxLayout()
        scan = QPushButton("Find Start Menu apps")
        scan.clicked.connect(lambda: self.send_intent("discover_apps"))
        self.discovered_apps = QComboBox()
        approve = QPushButton("Review approval")
        approve.clicked.connect(lambda: self.send_intent("approve_app", index=self.discovered_apps.currentIndex()))
        scan_row.addWidget(scan)
        scan_row.addWidget(self.discovered_apps, 1)
        scan_row.addWidget(approve)
        lay.addLayout(scan_row)
        self.apps_feedback = QLabel("Search Desktop, Documents, Downloads, or your selected music folder.")
        self.apps_feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.apps_feedback.setWordWrap(True)
        self.apps_feedback.setObjectName("note")
        lay.addWidget(self.apps_feedback)
        folder_row = QHBoxLayout()
        approve_folder = QPushButton("Approve another search folder")
        approve_folder.clicked.connect(self.approve_search_folder)
        folder_row.addWidget(approve_folder)
        reset_folders = QPushButton("Clear extra search folders")
        reset_folders.clicked.connect(lambda: self.save_settings(search_folders=[]))
        folder_row.addWidget(reset_folders)
        folder_row.addStretch()
        lay.addLayout(folder_row)
        return page

    def approve_search_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Approve a local folder for search")
        if not path:
            return
        if not local_directory(path, PathEnv.from_environ()):
            self.apps_feedback.setText("Choose a local folder outside protected Windows locations.")
            return
        folders = self.settings.get("search_folders")
        if path in folders:
            return
        if len(folders) >= 8:
            self.apps_feedback.setText("Clear extra folders before approving more than eight.")
            return
        self.save_settings(search_folders=folders + [path])
        self.apps_feedback.setText("Approved the selected folder for search.")

    def refresh_apps(self):
        approvals = getattr(self.engine, "app_approvals", None)
        if approvals is not None:
            selected = self.approved_apps.currentText()
            self.approved_apps.clear()
            self.approved_apps.addItems([entry.name for entry in approvals.registry.entries])
            if selected:
                self.approved_apps.setCurrentText(selected)

    def _settings_page(self):
        page = QWidget()
        box = QFormLayout(page)
        title = QLineEdit(self.settings.get("title"))
        title.setMaxLength(60)
        title.editingFinished.connect(lambda: self.save_settings(title=title.text().strip() or "Jarvix Personal AI"))
        box.addRow("Display title", title)
        greeting = QLineEdit(self.settings.get("greeting"))
        greeting.setMaxLength(180)
        greeting.editingFinished.connect(lambda: self.save_settings(greeting=greeting.text().strip()))
        box.addRow("Startup greeting", greeting)
        study_url = QLineEdit(self.settings.get("study_url"))
        study_url.setPlaceholderText("https://…")
        study_url.editingFinished.connect(lambda: self.save_settings(study_url=study_url.text().strip()))
        box.addRow("Study page", study_url)
        theme = QComboBox()
        theme.addItem("Cyan", "cyan")
        theme.addItem("Gold", "gold")
        theme.setCurrentIndex(theme.findData(self.settings.get("theme")))
        theme.currentIndexChanged.connect(lambda: self.save_settings(theme=theme.currentData()))
        box.addRow("Accent", theme)
        backdrop_row = QHBoxLayout()
        choose_backdrop = QPushButton("Choose local image or video")
        def choose_background():
            path, _ = QFileDialog.getOpenFileName(self, "Choose dashboard background", "",
                                                  "Images or videos (*.png *.jpg *.jpeg *.bmp *.webp *.mp4 *.webm *.wmv)")
            if path:
                self.save_settings(background=path)
        choose_backdrop.clicked.connect(choose_background)
        backdrop_row.addWidget(choose_backdrop)
        clear_backdrop = QPushButton("Use default")
        clear_backdrop.clicked.connect(lambda: self.save_settings(background=""))
        backdrop_row.addWidget(clear_backdrop)
        box.addRow("Dashboard background", backdrop_row)
        for key, label in (
            ("reduced_motion", "Reduce motion"), ("show_orb", "Show floating orb"),
            ("notifications", "Show tray reminder notifications"),
            ("close_to_tray", "Keep running in the tray when the window closes"),
            ("privacy", "Redact sensitive details in the command log"),
        ):
            check = QCheckBox(label)
            check.setChecked(self.settings.get(key))
            check.toggled.connect(lambda checked, name=key: self.save_settings(**{name: checked}))
            box.addRow(check)
        self.language_settings(box)
        self.voice_settings(box)
        self.brain_settings(box)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        return scroll

    def _diagnostics_page(self):
        page = QWidget()
        box = QVBoxLayout(page)
        self.diagnostics = QPlainTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setPlainText(self.diagnostic_text([]))
        box.addWidget(self.diagnostics, 1)
        row = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.poller.refresh)
        copy = QPushButton("Copy diagnostics")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.diagnostics.toPlainText()))
        row.addWidget(refresh)
        row.addWidget(copy)
        network = QPushButton("Test internet")
        network.clicked.connect(lambda: self.send_intent("diagnostics_internet"))
        row.addWidget(network)
        export = QPushButton("Save redacted report")
        export.clicked.connect(self.export_diagnostics)
        row.addWidget(export)
        row.addStretch()
        box.addLayout(row)
        return page

    def export_diagnostics(self):
        profile = getattr(self.engine, "profile", None)
        if profile:
            try:
                path = write_report(profile, "diagnostics.txt", self.diagnostics.toPlainText())
            except OSError:
                self.diagnostics.appendPlainText("Could not save a diagnostic report.")
            else:
                self.diagnostics.appendPlainText(f"Saved {path.name} in the Jarvix profile.")

    def diagnostic_text(self, problems):
        return "\n".join(filter(None, (
            f"Jarvix {__version__} · Python {sys.version.split()[0]}",
            "Database connected" if getattr(self.engine, "tasks", None) else "Database unavailable",
            "Windows actions connected" if getattr(self.engine, "system", None) else "Windows actions unavailable",
            *dependency_status(self.settings),
            self.engine.gmail.status() if getattr(self.engine, "gmail", None) else "Gmail unavailable",
            "WhatsApp browser handoff ready" if getattr(self.engine, "whatsapp", None) else "WhatsApp unavailable",
            "Loopback Brain enabled" if self.settings.get("brain_enabled") else "Loopback Brain off",
            "Qt Multimedia available",
            "Companion listener active" if getattr(self.engine, "phone", None) and self.engine.phone.listener else "Companion listener off",
            getattr(self, "internet_status", "Internet check available on request."), self.settings.problem, *problems,
        )))

    def _history_page(self):
        page = QWidget()
        box = QVBoxLayout(page)
        self.history_view = QPlainTextEdit()
        self.history_view.setReadOnly(True)
        self.history_view.setMaximumBlockCount(100)
        box.addWidget(self.history_view, 1)
        clear = QPushButton("Clear command history")
        clear.clicked.connect(self.clear_history)
        box.addWidget(clear)
        return page

    def _about_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(QLabel(f"Jarvix Personal AI v{__version__}\nYour PC. Your Commands. Your Intelligence."))
        lay.addWidget(QLabel("Safe defaults: nothing sends messages or emails by itself; power and close need a typed yes; "
                             "voice, workflows and phones can never confirm."))
        lay.addWidget(QLabel("1.1.7 (checkpoint build): tasks and reminders, Windows apps, volume, stats, power plans, 12 workflows, desktop window."))
        lay.addStretch(1)
        return page

    # ------------------------------------------------------------ behaviour
    def log_line(self, who: str, text: str) -> None:
        if self.settings.get("privacy"):
            text = redact_text(text)
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log.appendPlainText(f"[{stamp}] {who}> {text}")

    def set_status(self, text: str) -> None:
        self.status.setText(self.locale.label(text))

    def _history_step(self, direction: int) -> None:
        if not self.history:
            return
        if self.hist_pos is None:
            self.hist_pos = len(self.history)
        self.hist_pos = max(0, min(len(self.history), self.hist_pos + direction))
        self.input.setText(self.history[self.hist_pos] if self.hist_pos < len(self.history) else "")

    def clear_transcript(self):
        self.transcribed = False

    def send_from_box(self) -> None:
        self.send_text(self.input.text(), source="voice" if self.transcribed else "typed")
        self.input.clear()
        self.clear_transcript()

    def send_text(self, text: str, show_home: bool = False, source="typed") -> None:
        text = (text or "").strip()
        if not text:
            return
        if show_home:
            self.nav.setCurrentRow(0)
        if self.locale.command(text).casefold().rstrip(".!") in {"pause jarvix", "jarvix pause"}:
            self.stop_active_services()
            self.cancel_workflow(quiet=True)
            self.worker.discard_queued()
        approval = text.casefold().rstrip(".!")
        if approval in {"yes", "y", "confirm", "ok", "okay", "sure", "do it", "go ahead"} and not self.preview_visible:
            self.log_line("jarvix", "There is no approval preview to confirm.")
            return
        self.preview_visible = False
        if not self.worker.submit(text, source):
            self.log_line("jarvix", "The command queue is full. Try again in a moment.")
            return
        if not self.history or self.history[-1] != text:
            if not self.settings.get("privacy"):
                self.history.append(text)
                self.history = self.history[-100:]
            self.history_view.appendPlainText(redact_text(text))
        self.hist_pos = None
        self.log_line("you", text)
        self.set_status(STATUS["working"])
        self.set_reactor_state("Thinking")

    def send_intent(self, name, **slots):
        self.preview_visible = False
        if not self.worker.submit(Intent(name, slots), "ui"):
            self.log_line("jarvix", "The command queue is full. Try again in a moment.")
            return
        self.set_status(STATUS["working"])
        self.set_reactor_state("Thinking")

    def on_status(self, key):
        self.set_status(STATUS.get(key, key))
        if key == "working":
            self.set_reactor_state("Executing")

    def set_reactor_state(self, state):
        self.dashboard.reactor.set_state(state)
        self.orb.set_state(state)

    def on_result(self, reply):
        if reply.key == "diagnostics.internet":
            self.internet_status = reply.text
            self.poller.refresh()
        if reply.key.startswith(("task.", "snooze.", "delete.")):
            self.task_panel.show_reply(reply)
        if reply.key in {"workflow.saved", "workflow.editor", "workflow.result", "workflow.unknown"}:
            self.workflow_editor.show_result(reply)
            if reply.key == "workflow.saved":
                for button, name in zip(self.workflow_buttons, self.engine.workflows.names()):
                    button.setText(name)
        if reply.key.startswith("phone."):
            self.phone_panel.reply(reply)
            if reply.key == "phone.invite":
                self.nav.setCurrentRow(NAV.index("Android"))
        if reply.key.startswith("whatsapp."):
            self.whatsapp_panel.show_reply(reply)
        elif reply.key.startswith("gmail."):
            self.gmail_panel.show_reply(reply)
            if reply.key in {"gmail.list", "gmail.preview"}:
                self.nav.setCurrentRow(NAV.index("Gmail"))
        elif reply.key.startswith("music."):
            self.music_panel.show_reply(reply)
            if reply.key == "music.no_match":
                self.nav.setCurrentRow(NAV.index("Music"))
        elif self.settings.get("speech") and not self.settings.get("privacy") and reply.key.startswith(
            ("app.opened", "volume.", "pause.resumed", "task.saved", "sysinfo")
        ):
            self.voice.say(reply.text)
        if self.engine.paused:
            self.stop_active_services()
        self.preview_visible = reply.kind == "confirm" or reply.key in {"confirm.typed", "confirm.source"}
        if reply.kind == "confirm":
            self.nav.setCurrentRow(0)
            self.input.setFocus()
            if self.settings.get("privacy"):
                dialog = QMessageBox(self)
                dialog.setWindowTitle("Review the requested action")
                dialog.setTextFormat(Qt.TextFormat.PlainText)
                dialog.setText(reply.text)
                dialog.exec()
        if reply.key == "search.results":
            self.search_results.clear()
            self.search_results.addItems([f"{hit['kind']}   {hit['path']}" for hit in reply.data["hits"]])
            self.apps_feedback.setText(f"{self.search_results.count()} results in approved locations.")
            self.nav.setCurrentRow(NAV.index("Apps"))
        elif reply.key == "apps.discovered":
            self.discovered_apps.clear()
            for app in reply.data["apps"]:
                self.discovered_apps.addItem(app["name"])
                self.discovered_apps.setItemData(self.discovered_apps.count() - 1, app["target"],
                                                 Qt.ItemDataRole.ToolTipRole)
            self.apps_feedback.setText(reply.text)
        elif reply.key == "apps.updated":
            self.refresh_apps()
            self.apps_feedback.setText(reply.text)
        elif reply.key.startswith(("apps.", "search.", "app.")):
            self.apps_feedback.setText(reply.text)

    @Slot(str, str, str)
    def on_reply(self, kind: str, text: str, command: str) -> None:
        self.log_line("jarvix", text)
        self.set_status(STATUS.get(kind, kind))
        self.refresh_pause_label()
        state = ("Paused" if self.engine.paused else
                 "Error" if kind in {"error", "refused", "unavailable", "unknown"} else
                 "Success" if kind == "done" else "Idle")
        self.set_reactor_state(state)
        self.state_timer.start(1700)
        if self.isVisible():
            self.poller.refresh()
        if kind == "exit":
            self.exit()

    @Slot(str)
    def on_alert(self, text: str) -> None:
        self.log_line("ALERT", text)
        display = redact_text(text) if self.settings.get("privacy") else text
        self.notification.setText(display)
        if self.tray is not None and self.settings.get("notifications"):
            self.tray.showMessage("Jarvix reminder", display)

    def toggle_pause(self) -> None:
        if self.engine.paused:
            self.worker.submit("resume jarvix")
        else:
            self.preview_visible = False
            self.stop_active_services()
            self.cancel_workflow(quiet=True)
            self.worker.discard_queued()
            self.worker.submit("pause jarvix")

    def cancel_workflow(self, _checked=False, *, quiet=False) -> None:
        svc = getattr(self.engine, "workflows", None)
        if svc is not None and svc.cancel_running():
            if not quiet:
                self.log_line("jarvix", "Cancel requested.")
        elif not quiet:
            self.log_line("jarvix", "No workflow is running.")

    def refresh_pause_label(self) -> None:
        label = "Resume Jarvix" if self.engine.paused else "Pause Jarvix"
        self.pause_btn.setText(self.locale.label(label))
        if self.tray is not None:
            self.tray_pause.setText(self.locale.label(label))

    def on_dashboard(self, data):
        self.dashboard.show_metrics(data["metrics"])
        for label, items, empty in (
            (self.today, data["tasks"], "No tasks due today"),
            (self.upcoming, data["reminders"], "No upcoming reminders"),
        ):
            first = items[0] if items else self.locale.label(empty)
            label.setText((first[:66] + "…" if len(first) > 66 else first) +
                          (f"  (+{len(items) - 1})" if len(items) > 1 else ""))
            label.setToolTip("\n".join(items))
        self.diagnostics.setPlainText(self.diagnostic_text(data["problems"]))

    def clear_history(self):
        self.history.clear()
        self.hist_pos = None
        self.history_view.clear()

    def save_settings(self, **changes):
        try:
            if changes.get("brain_endpoint"):
                brain_endpoint(changes["brain_endpoint"])
            self.settings.update(**changes)
        except (OSError, ValueError):
            self.log_line("jarvix", "Could not save settings.")
            return
        if changes.get("privacy"):
            self.clear_history()
            self.log.clear()
        if {"voice_provider", "wake_word", "vosk_model"} & changes.keys():
            self.voice.stop()
        if "language" in changes:
            self.locale = Locale(changes["language"])
            if getattr(self.engine, "parser", None) is not None:
                self.engine.parser.locale = self.locale
            self.apply_language()
        if "hotkey" in changes:
            self.hotkey.setKey(QKeySequence(changes["hotkey"]))
            if self.native_hotkey is not None:
                self.hotkey.setEnabled(not self.native_hotkey.register(changes["hotkey"]))
        if {"title", "theme", "show_orb", "reduced_motion", "background"} & changes.keys():
            self.apply_appearance()

    def apply_appearance(self):
        title = self.settings.get("title") or "Jarvix Personal AI"
        self.setWindowTitle(f"{title} v{__version__}")
        self.title.setText(title)
        accent = "#ffc857" if self.settings.get("theme") == "gold" else "#7ff3ff"
        self.setStyleSheet(STYLE.replace("#7ff3ff", accent))
        for reactor in (self.dashboard.reactor, self.orb):
            reactor.accent = accent
            reactor.set_static(self.settings.get("reduced_motion"))
        self.battle_panel.update_view(static=self.settings.get("reduced_motion"))
        self.backdrop.load(self.settings.get("background"), self.settings.get("reduced_motion"))
        if self.isVisible():
            self.show_orb()

    def show_orb(self):
        if self.settings.get("show_orb"):
            self.orb.place(self.settings.get("orb_x"), self.settings.get("orb_y"))
            self.orb.show()
        else:
            self.orb.hide()

    def push_to_talk(self):
        self.restore()
        if self.engine.paused:
            self.voice_info("Resume Jarvix before recording.")
        else:
            self.voice.push()

    def stop_active_services(self):
        self.voice.stop()
        gmail = getattr(self.engine, "gmail", None)
        if gmail is not None:
            gmail.stop()
        if self.phone_panel.link and self.phone_panel.link.listener:
            self.phone_panel.stop()
        if self.music_panel.player is not None:
            self.music_panel.player.stop()

    def voice_info(self, text):
        self.dashboard.voice.setText(text)
        self.log_line("jarvix", text)

    def voice_transcript(self, text):
        self.restore()
        self.input.setText(text[:2000])
        self.transcribed = True
        self.dashboard.voice.setText("Review the transcript, then press Send")
        self.input.setFocus()

    def on_voice_state(self, state):
        self.voice_state = state
        self.mic.setText("Finish recording" if state == "Listening" else "Push to talk")
        if state == "Listening":
            online = self.settings.get("voice_provider") == "google"
            self.dashboard.voice.setText("Recording · Google recognition" if online else "Recording · Offline")
        elif state == "Thinking":
            self.dashboard.voice.setText("Recognizing speech")
        elif state == "Speaking":
            self.dashboard.voice.setText("Speaking")
        elif self.dashboard.voice.text() in {"Recording · Google recognition", "Recording · Offline",
                                             "Recognizing speech", "Speaking"}:
            self.dashboard.voice.setText("Microphone off")
        self.reset_reactor()

    def reset_reactor(self):
        self.set_reactor_state("Paused" if self.engine.paused else self.voice_state)

    def language_settings(self, box):
        languages = QComboBox()
        for key, name in LANGUAGES.items():
            languages.addItem(name, key)
        languages.setCurrentIndex(languages.findData(self.locale.key))
        languages.currentIndexChanged.connect(lambda: self.save_settings(language=languages.currentData()))
        box.addRow("Language", languages)
        review = QLabel("Translations need native review. Type Help for command examples.")
        review.setWordWrap(True)
        review.setObjectName("note")
        box.addRow(review)

    def apply_language(self):
        for index, source in enumerate(NAV):
            self.nav.item(index).setText(self.locale.label(source))
        for widget in (*self.findChildren(QLabel), *self.findChildren(QPushButton)):
            if widget in {self.title, self.status, self.pause_btn, self.today, self.upcoming}:
                continue
            source = widget.property("jarvixLabel") or widget.text()
            if source in self.locale.data["ui"]:
                widget.setProperty("jarvixLabel", source)
                widget.setText(self.locale.label(source))
        self.refresh_pause_label()

    def voice_settings(self, box):
        provider = QComboBox()
        for label, key in (("Off", "off"), ("Vosk (offline)", "vosk"), ("Google (sends audio)", "google")):
            provider.addItem(label, key)
        provider.setCurrentIndex(provider.findData(self.settings.get("voice_provider")))
        provider.currentIndexChanged.connect(lambda: self.save_settings(voice_provider=provider.currentData()))
        box.addRow("Microphone provider", provider)
        model = QLineEdit(self.settings.get("vosk_model"))
        model.editingFinished.connect(lambda: self.save_settings(vosk_model=model.text().strip()))
        model_row = QHBoxLayout()
        model_row.addWidget(model, 1)
        choose = QPushButton("Choose folder")
        def choose_model():
            folder = QFileDialog.getExistingDirectory(self, "Choose an extracted Vosk model")
            if folder:
                model.setText(folder)
                self.save_settings(vosk_model=folder)
        choose.clicked.connect(choose_model)
        model_row.addWidget(choose)
        box.addRow("Offline model", model_row)
        install_model = QPushButton("Download verified offline model")
        def open_download():
            def selected(path):
                model.setText(path)
                self.save_settings(vosk_model=path)
            ModelDialog(self, getattr(self.engine, "profile", None), selected).exec()
        install_model.clicked.connect(open_download)
        box.addRow(install_model)
        wake = QCheckBox("Allow offline wake-word listening")
        wake.setChecked(self.settings.get("wake_word"))
        wake.toggled.connect(lambda state: self.save_settings(wake_word=state))
        box.addRow(wake)
        wake_button = QPushButton("Start or stop wake-word listening")
        wake_button.clicked.connect(lambda: self.voice.start_wake() if not self.engine.paused else
                                    self.voice_info("Resume Jarvix before recording."))
        box.addRow(wake_button)
        hotkeys = QComboBox()
        hotkeys.addItems(list(CHORDS))
        hotkeys.setCurrentText(self.settings.get("hotkey"))
        hotkeys.currentTextChanged.connect(lambda value: self.save_settings(hotkey=value))
        box.addRow("Push-to-talk shortcut", hotkeys)
        speech = QCheckBox("Enable Windows speech output")
        speech.setChecked(self.settings.get("speech"))
        speech.toggled.connect(lambda state: self.save_settings(speech=state))
        box.addRow(speech)
        voices = QComboBox()
        voices.addItem("System default", "")
        current = self.settings.get("speech_voice")
        if current:
            voices.addItem("Saved voice", current)
            voices.setCurrentIndex(1)
        voices.currentIndexChanged.connect(lambda: self.save_settings(speech_voice=voices.currentData()))
        def populate(options):
            current = self.settings.get("speech_voice")
            old = voices.blockSignals(True)
            voices.clear()
            voices.addItem("System default", "")
            for identifier, name in options:
                voices.addItem(name, identifier)
            voices.setCurrentIndex(max(0, voices.findData(current)))
            voices.blockSignals(old)
        self.voice.voice_list.connect(populate)
        row = QHBoxLayout()
        row.addWidget(voices, 1)
        detect = QPushButton("Detect voices")
        detect.clicked.connect(self.voice.detect_voices)
        row.addWidget(detect)
        box.addRow("Speech voice", row)
        for key, label, limits in (("speech_rate", "Speech rate", (80, 350)),
                                   ("speech_volume", "Speech volume", (0, 100))):
            spin = QSpinBox()
            spin.setRange(*limits)
            spin.setValue(self.settings.get(key))
            spin.valueChanged.connect(lambda value, name=key: self.save_settings(**{name: value}))
            box.addRow(label, spin)
        test = QPushButton("Test speech")
        test.clicked.connect(lambda: self.voice.say("Jarvix speech output is active.", test=True)
                             if not self.engine.paused else self.voice_info("Resume Jarvix to test speech."))
        box.addRow(test)

    def restore(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.nav.setCurrentRow(0)
        self.input.setFocus()

    def brain_settings(self, box):
        enabled = QCheckBox("Enable the loopback-only Jarvix Brain")
        enabled.setChecked(self.settings.get("brain_enabled"))
        enabled.toggled.connect(lambda active: self.save_settings(brain_enabled=active))
        box.addRow(enabled)
        address = QLineEdit(self.settings.get("brain_endpoint"))
        address.setPlaceholderText("http://127.0.0.1:11434")
        address.editingFinished.connect(lambda: self.save_settings(brain_endpoint=address.text().strip()))
        box.addRow("Brain endpoint", address)
        model = QLineEdit(self.settings.get("brain_model"))
        model.setPlaceholderText("Installed model name")
        model.editingFinished.connect(lambda: self.save_settings(brain_model=model.text().strip()))
        box.addRow("Brain model", model)
        test = QPushButton("Test local Brain")
        test.clicked.connect(lambda: (self.nav.setCurrentRow(0), self.send_intent(
            "brain_ask", prompt="Introduce yourself in one short sentence."
        )))
        box.addRow(test)

    def start_desktop_services(self):
        if QApplication.platformName() == "windows":
            self.native_hotkey = Hotkey(int(self.winId()), self.push_to_talk)
            self.hotkey.setEnabled(not self.native_hotkey.register(self.settings.get("hotkey")))
        if self.settings.get("greeting"):
            self.log_line("jarvix", self.settings.get("greeting"))
        music = getattr(self.engine, "music", None)
        if music:
            music.available = True
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.windowIcon(), self)
            self.tray.setToolTip("Jarvix Personal AI")
            menu = QMenu(self)
            menu.addAction("Open Jarvix", self.restore)
            menu.addAction("Push to talk", self.push_to_talk)
            self.tray_pause = menu.addAction("Pause Jarvix", self.toggle_pause)
            menu.addAction("Exit Jarvix", self.exit)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(lambda reason: self.restore()
                                        if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
            self.tray.show()
        self.show_orb()
        self.poller.start()

    def stop_desktop_services(self):
        self.state_timer.stop()
        if self.native_hotkey is not None:
            self.native_hotkey.close()
        self.stop_active_services()
        self.voice.shutdown()
        self.backdrop.stop()
        self.orb.hide()
        if self.tray is not None:
            self.tray.hide()
        self.poller.stop()

    def exit(self):
        self.exiting = True
        self.close()
        QApplication.quit()

    def closeEvent(self, event) -> None:
        if not self.exiting and self.tray is not None and self.settings.get("close_to_tray"):
            self.hide()
            event.ignore()
            return
        self.cancel_workflow(quiet=True)
        self.stop_active_services()
        self.orb.hide()
        super().closeEvent(event)

def run_gui(engine, workflow_names: list, start_services: Callable) -> int:
    """Keep scheduler and database alive until the worker has fully exited."""
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    bridge = Bridge()
    worker = CommandWorker(engine, bridge)
    win = MainWindow(engine, bridge, worker, workflow_names)
    svc = getattr(engine, "workflows", None)
    if svc is not None:
        svc.runner.on_step = bridge.workflow_step.emit
        svc.runner.notify = bridge.notification.emit
        def workflow_speak(text):
            if not win.settings.get("speech"):
                return "unavailable", "Enable speech output in Settings."
            bridge.speak.emit(text)
            return "ok", "Speech requested."
        svc.runner.speak = workflow_speak
        if getattr(engine, "music", None):
            def workflow_music(query):
                reply, _ = engine.music.handle(Intent("music_play", {"query": query}), datetime.now())
                bridge.music.emit(reply)
                return ("ok", "Local playback requested.") if reply.key == "music.request" else ("unavailable", reply.text)
            svc.runner.play = workflow_music
    cleanup = None
    worker_started = False
    try:
        cleanup = start_services(bridge)
        worker.start()
        worker_started = True
        win.show()
        win.start_desktop_services()
        return app.exec()
    finally:
        win.stop_desktop_services()
        if worker_started:
            # Also cancel if the event loop exits for reasons other than closeEvent.
            svc = getattr(engine, "workflows", None)
            if svc is not None:
                svc.cancel_running()
            if not worker.stop():
                # Never destroy an active QThread or release its SQLite connection.
                print("Jarvix is completing an active command before safe shutdown...")
                worker.wait()  # Qt has no safe force-kill for Python command handlers.
        if cleanup is not None:
            cleanup()


