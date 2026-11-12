"""Desktop controls for music, Gmail and WhatsApp."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from ..core.safety import PathEnv, check_mutation_path


def button(label, callback):
    item = QPushButton(label)
    item.clicked.connect(callback)
    return item


def heading(layout, title):
    label = QLabel(title)
    label.setObjectName("title")
    layout.addWidget(label)


def feedback():
    label = QLabel()
    label.setObjectName("note")
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class WhatsAppPanel(QWidget):
    def __init__(self, service, settings, save_settings, submit):
        super().__init__()
        self.service = service
        self.submit = submit
        layout = QVBoxLayout(self)
        heading(layout, "WhatsApp")
        form = QFormLayout()
        self.contact = QComboBox()
        self.contact.setEditable(True)
        self.contact.lineEdit().setMaxLength(60)
        self.contact.activated.connect(self.load_contact)
        self.number = QLineEdit()
        self.number.setPlaceholderText("+91 …")
        self.number.setMaxLength(30)
        self.aliases = QLineEdit()
        self.aliases.setPlaceholderText("Comma-separated aliases")
        self.busy_allowed = QCheckBox("Allow Busy Mode drafts for this contact")
        for label, field in (("Name or number", self.contact), ("International number", self.number),
                             ("Aliases", self.aliases)):
            form.addRow(label, field)
        form.addRow(self.busy_allowed)
        form.addRow(button("Save contact", self.save_contact))
        layout.addLayout(form)
        self.message = QPlainTextEdit()
        self.message.setPlaceholderText("Write a WhatsApp message")
        self.message.setAccessibleName("WhatsApp message")
        layout.addWidget(self.message, 1)
        compose = QHBoxLayout()
        for label, intent in (("Review message", "whatsapp_message"), ("Review call handoff", "whatsapp_call"),
                              ("Review Busy Mode draft", "busy_mode_draft")):
            compose.addWidget(button(label, lambda _checked=False, name=intent: self.submit(
                name, contact=self.contact.currentText(), message=self.message.toPlainText() if name == "whatsapp_message" else ""
            )))
        layout.addLayout(compose)
        busy = QHBoxLayout()
        template = QLineEdit(settings.get("busy_reply"))
        template.setMaxLength(500)
        template.setPlaceholderText("Busy Mode reply template")
        template.editingFinished.connect(lambda: save_settings(busy_reply=template.text().strip()))
        busy.addWidget(template, 1)
        busy.addWidget(button("Enable Busy Mode", lambda: self.submit("busy_mode_enable")))
        busy.addWidget(button("Disable", lambda: self.submit("busy_mode_disable")))
        layout.addLayout(busy)
        self.status = feedback()
        self.status.setText("Jarvix opens the chat after you review it. Send and call from WhatsApp.")
        layout.addWidget(self.status)
        self.reload()

    def reload(self):
        if self.service:
            name = self.contact.currentText()
            self.contact.clear()
            self.contact.addItems(sorted(contact["name"] for contact in self.service.contacts.values()))
            self.contact.setEditText(name)

    def load_contact(self, index):
        if self.service:
            item = self.service.contacts.get(self.contact.itemText(index).casefold())
            if item:
                self.number.setText("+" + item["number"])
                self.aliases.setText(", ".join(item["aliases"]))
                self.busy_allowed.setChecked(item["busy"])

    def save_contact(self):
        self.submit("whatsapp_contact", name=self.contact.currentText(), number=self.number.text(),
                    aliases=self.aliases.text(), busy=self.busy_allowed.isChecked())

    def show_reply(self, reply):
        if reply.key == "whatsapp.contacts_updated":
            self.reload()
        if reply.kind != "confirm":
            self.status.setText(reply.text)


class GmailPanel(QWidget):
    def __init__(self, service, submit):
        super().__init__()
        self.service, self.submit = service, submit
        layout = QVBoxLayout(self)
        heading(layout, "Gmail")
        row = QHBoxLayout()
        self.accounts = QComboBox()
        self.accounts.setAccessibleName("Connected Gmail account")
        self.accounts.activated.connect(lambda _index: submit("gmail_select", id=self.accounts.currentData()))
        row.addWidget(self.accounts, 1)
        row.addWidget(button("Connect account", self.connect_account))
        row.addWidget(button("Disconnect", lambda: submit("gmail_disconnect", id=self.accounts.currentData())))
        layout.addLayout(row)
        browse = QHBoxLayout()
        for label, name in (("Unread count", "gmail_unread"), ("Recent inbox", "gmail_latest"),
                            ("Drafts", "gmail_drafts"), ("Read selected", "gmail_read")):
            browse.addWidget(button(label, lambda _checked=False, action=name: submit(
                action, index=self.messages.currentRow()
            )))
        layout.addLayout(browse)
        self.messages = QListWidget()
        self.messages.setAccessibleName("Gmail subjects")
        self.messages.itemDoubleClicked.connect(lambda _item: submit("gmail_read", index=self.messages.currentRow()))
        layout.addWidget(self.messages, 1)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("Selected email preview")
        layout.addWidget(self.preview, 1)
        form = QFormLayout()
        self.to, self.subject = QLineEdit(), QLineEdit()
        self.to.setMaxLength(200)
        self.subject.setMaxLength(250)
        self.body = QPlainTextEdit()
        self.body.setMaximumHeight(75)
        form.addRow("Recipient", self.to)
        form.addRow("Subject", self.subject)
        form.addRow("Message", self.body)
        form.addRow(button("Review Gmail compose", lambda: submit(
            "gmail_compose", to=self.to.text(), subject=self.subject.text(), body=self.body.toPlainText()
        )))
        layout.addLayout(form)
        self.status = feedback()
        layout.addWidget(self.status)
        self.reload()

    def connect_account(self):
        path, _filter = QFileDialog.getOpenFileName(self, "Choose Google Desktop OAuth client JSON", "",
                                                   "JSON files (*.json)")
        if path:
            self.status.setText("Follow the Google sign-in in your browser.")
            self.submit("gmail_connect", path=path)

    def reload(self):
        if self.service:
            self.accounts.clear()
            for ident, account in self.service.accounts.items():
                self.accounts.addItem(account, ident)
            self.accounts.setCurrentIndex(self.accounts.findData(self.service.selected))
            self.messages.clear()
            self.preview.clear()
            self.status.setText(self.service.status())
        else:
            self.status.setText("Gmail connection is unavailable.")

    def show_reply(self, reply):
        if reply.key == "gmail.accounts":
            self.reload()
        if reply.key == "gmail.list":
            self.messages.clear()
            self.messages.addItems(reply.data["subjects"])
            self.preview.clear()
        if reply.key == "gmail.preview":
            self.preview.setPlainText(reply.data["preview"])
        if reply.kind != "confirm":
            self.status.setText(reply.text)


class MusicPanel(QWidget):
    def __init__(self, settings, save_settings, submit):
        super().__init__()
        self.settings, self.save_settings, self.submit = settings, save_settings, submit
        self.player = self.output = None
        self.tracks = []
        self.query = ""
        layout = QVBoxLayout(self)
        heading(layout, "Music")
        choose = QHBoxLayout()
        self.folder = QLineEdit(settings.get("music_folder"))
        self.folder.setReadOnly(True)
        self.folder.setPlaceholderText("Choose a local music folder")
        choose.addWidget(self.folder, 1)
        choose.addWidget(button("Choose folder", self.choose_folder))
        choose.addWidget(button("Refresh tracks", lambda: submit("music_scan")))
        layout.addLayout(choose)
        self.list = QListWidget()
        self.list.setAccessibleName("Local tracks")
        self.list.itemDoubleClicked.connect(lambda _item: submit("music_select", index=self.list.currentRow()))
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        for label, action in (("Previous", "previous"), ("Play", "play"), ("Pause", "pause"),
                              ("Resume", "resume"), ("Next", "next"), ("Stop", "stop")):
            row.addWidget(button(label, lambda _checked=False, name=action: submit(
                "music_select" if name == "play" else "music_" + name, index=self.list.currentRow()
            )))
        layout.addLayout(row)
        self.search = button("Search YouTube", lambda: submit("youtube_search", query=self.query))
        self.search.setEnabled(False)
        layout.addWidget(self.search)
        self.status = feedback()
        self.status.setText("Choose your music folder to find tracks.")
        layout.addWidget(self.status)

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Approve a local music folder")
        if path:
            self.save_settings(music_folder=str(Path(path)))
            self.folder.setText(str(Path(path)))
            self.submit("music_scan")

    def load(self, paths):
        self.tracks = paths
        self.list.clear()
        self.list.addItems([os.path.basename(path) for path in paths])
        if paths:
            self.list.setCurrentRow(0)

    def play(self, index):
        if not 0 <= index < len(self.tracks):
            self.status.setText("Select a local track first.")
            return
        path = self.tracks[index]
        root = self.settings.get("music_folder")
        if not os.path.isfile(path) or not check_mutation_path(path, [root], PathEnv.from_environ()).allowed:
            self.status.setText("The track is outside the approved folder or has moved.")
            return
        if self.player is None:
            self.player, self.output = QMediaPlayer(self), QAudioOutput(self)
            self.player.setAudioOutput(self.output)
            self.output.setVolume(.55)
            self.player.playbackStateChanged.connect(self.playback_state)
            self.player.mediaStatusChanged.connect(self.media_status)
            self.player.errorOccurred.connect(lambda _err, _msg: self.status.setText(
                "The audio backend could not play this track."
            ))
        self.list.setCurrentRow(index)
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        self.status.setText(f"Loading {os.path.basename(path)}")

    def control(self, name):
        if name in {"next", "previous"} and self.tracks:
            self.play((self.list.currentRow() + (1 if name == "next" else -1)) % len(self.tracks))
        elif name == "play":
            self.play(self.list.currentRow())
        elif self.player is not None:
            {"stop": self.player.stop, "pause": self.player.pause, "resume": self.player.play}[name]()
        else:
            self.status.setText("There is no active local track.")

    def playback_state(self, state):
        text = {"PlayingState": "Playing", "PausedState": "Paused", "StoppedState": "Stopped"}
        self.status.setText(text[state.name])

    def media_status(self, state):
        if state == QMediaPlayer.MediaStatus.EndOfMedia and self.list.currentRow() + 1 < len(self.tracks):
            self.control("next")

    def show_reply(self, reply):
        self.status.setText(reply.text)
        if reply.key == "music.no_match":
            self.query = reply.data.get("query", "")
            self.search.setEnabled(bool(self.query))
        elif reply.key == "music.request":
            data = reply.data
            if data["action"] in {"scan", "play"}:
                self.load(data["tracks"])
            if data["action"] == "select":
                self.play(data["index"])
            elif data["action"] != "scan":
                self.control(data["action"])
