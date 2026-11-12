"""Contact validation and reviewed WhatsApp browser handoff."""
from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

from ..config.settings import read_json, write_json
from ..core.intents import Reply

_DIGITS = re.compile(r"[1-9][0-9]{7,14}\Z")


def international_number(value):
    if not isinstance(value, str):
        raise ValueError("Use an international phone number with its country code.")
    text = value.strip()
    if not re.fullmatch(r"\+?[0-9 ()-]+", text):
        raise ValueError("Use an international phone number with its country code.")
    digits = re.sub(r"[^0-9]", "", text)
    if not _DIGITS.fullmatch(digits):
        raise ValueError("Use an international phone number with its country code.")
    return digits


def whatsapp_url(phone, message=""):
    digits = international_number(phone)
    if not isinstance(message, str) or len(message) > 1_800 or "\x00" in message:
        raise ValueError("Use a message up to 1,800 characters.")
    return f"https://wa.me/{digits}" + ("?" + urlencode({"text": message}) if message else "")


class WhatsApp:
    def __init__(self, path, catalog, settings, open_url):
        self.path, self.catalog, self.settings, self.open_url = Path(path), catalog, settings, open_url
        self.sample_names = dict(catalog.contacts)
        self.contacts = {}
        self.problem = ""
        self.cooldowns = {}
        try:
            values = read_json(self.path)
            if not isinstance(values, list) or len(values) > 100:
                raise ValueError("Invalid contact list.")
            for value in values:
                contact = self.validate_contact(value)
                self.contacts[contact["name"].casefold()] = contact
        except FileNotFoundError:
            pass
        except (ValueError, OSError, TypeError, AttributeError):
            self.contacts.clear()
            self.problem = "Contacts could not be read."
        self._refresh()

    def _refresh(self):
        self.catalog.contacts = {**self.sample_names,
                                 **{item["name"]: item["aliases"] for item in self.contacts.values()}}
        self.catalog.__post_init__()

    @staticmethod
    def validate_contact(data):
        name = data.get("name", "").strip()
        if not 1 <= len(name) <= 60 or any(ord(c) < 32 for c in name):
            raise ValueError("Enter a contact name up to 60 characters.")
        raw = data.get("aliases", [])
        if isinstance(raw, str):
            raw = [alias.strip() for alias in raw.split(",") if alias.strip()]
        if not isinstance(raw, list) or len(raw) > 6 or any(
            not isinstance(alias, str) or not 1 <= len(alias) <= 40 or any(ord(c) < 32 for c in alias)
            for alias in raw
        ):
            raise ValueError("Use up to six short, comma-separated aliases.")
        return {"name": name, "number": international_number(data.get("number")),
                "aliases": raw, "busy": data.get("busy") is True}

    def save_contact(self, intent, _now):
        try:
            contact = self.validate_contact(intent.slots)
            contacts = {**self.contacts, contact["name"].casefold(): contact}
            if len(contacts) > 100:
                raise ValueError("The contact list already has 100 entries.")
            write_json(self.path, list(contacts.values()))
            self.contacts = contacts
            self._refresh()
            self.problem = ""
        except (ValueError, OSError) as exc:
            return Reply("error", str(exc), "whatsapp.contact"), None
        return Reply("done", f"Saved {contact['name']}.", "whatsapp.contacts_updated"), None

    def handle(self, intent, now):
        if intent.name == "whatsapp_unsupported":
            return Reply("unavailable", "Use WhatsApp to read messages or start a call after opening a chat.",
                         "whatsapp.unsupported"), None
        if intent.name == "busy_mode_enable":
            return self.enable_busy(now)
        if intent.name == "busy_mode_disable":
            self.settings.update(busy_enabled=False)
            return Reply("done", "Busy Mode is off.", "whatsapp.busy"), None
        contact = intent.get("contact") or intent.get("contact_query") or ""
        entry = self.contacts.get(contact.casefold())
        if entry is None:
            matches = [item for item in self.contacts.values() if
                       contact.casefold() in {alias.casefold() for alias in item["aliases"]}]
            entry = matches[0] if len(matches) == 1 else None
            if len(matches) > 1:
                return Reply("error", "More than one contact uses that alias. Enter the full name.", "whatsapp.alias"), None
        if entry is None:
            try:
                number = international_number(contact)
            except ValueError:
                return Reply("unavailable", "Add the contact and country code on the WhatsApp page.",
                             "whatsapp.setup"), None
            entry = {"name": "Entered number", "number": number, "busy": False}
        message = intent.get("message", "")
        if intent.name == "busy_mode_draft":
            key = entry["number"]
            if not self.settings.get("busy_enabled") or not entry["busy"]:
                return Reply("refused", "Enable Busy Mode and approve this contact first.", "whatsapp.busy"), None
            if self.cooldowns.get(key) and now - self.cooldowns[key] < timedelta(seconds=60):
                return Reply("info", "A draft was already offered for this contact in the last minute.", "whatsapp.busy"), None
            message = self.settings.get("busy_reply")
            self.cooldowns[key] = now
        try:
            url = whatsapp_url(entry["number"], message)
        except ValueError as exc:
            return Reply("error", str(exc), "whatsapp.invalid"), None
        recipient = f"{entry['name']} (+{entry['number']})"
        handoff = "Open this WhatsApp chat and use WhatsApp to start the call." if intent.name == "whatsapp_call" else (
            "Open a WhatsApp draft. Press Send inside WhatsApp." if message else "Open this WhatsApp chat."
        )
        details = f"WhatsApp recipient: {recipient}\n" + (f"Message: {message}\n" if message else "") + handoff

        def act(_confirmed_at):
            if not self.open_url(url):
                return Reply("error", "The browser could not open WhatsApp.", "whatsapp.failed")
            return Reply("done", handoff, "whatsapp.opened")

        return Reply("confirm", details + "\nType yes to continue.", "whatsapp.preview"), act

    def enable_busy(self, _now):
        template = self.settings.get("busy_reply")
        approved = [item["name"] for item in self.contacts.values() if item["busy"]]
        if not template or not approved:
            return Reply("error", "Save a reply template and approve at least one contact.", "whatsapp.busy"), None
        def enable(_confirmed_at):
            self.settings.update(busy_enabled=True)
            return Reply("done", "Busy Mode drafts are available.", "whatsapp.busy")
        return Reply("confirm", f"Enable Busy Mode for {', '.join(approved)}?\nMessage: {template}\nType yes to confirm.",
                     "whatsapp.busy_preview"), enable
