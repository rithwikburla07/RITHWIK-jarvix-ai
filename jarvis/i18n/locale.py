"""Localized labels and command aliases with one shared parser and policy."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from ..config.settings import read_json

LANGUAGES = {
    "en": "English", "hi": "हिन्दी", "ru": "Русский", "uk": "Українська",
    "pt": "Português", "tr": "Türkçe", "it": "Italiano", "de": "Deutsch",
}
ROOT = Path(__file__).resolve().parents[2] / "data" / "lang"


def decimal_digits(text):
    return "".join(str(unicodedata.decimal(char)) if char.isdecimal() else char for char in text)


class Locale:
    def __init__(self, key="en"):
        self.key = key if key in LANGUAGES else "en"
        self.data = read_json(ROOT / f"{self.key}.json")
        self.exact = {text.casefold(): canonical for text, canonical in self.data["commands"].items()}
        self.prefixes = sorted(self.data["prefixes"].items(), key=lambda item: -len(item[0]))
        self.when = sorted(self.data["time_words"].items(), key=lambda item: -len(item[0]))

    def label(self, text):
        return self.data["ui"].get(text, text)

    def time_text(self, text):
        result = decimal_digits(text)
        for old, new in self.when:
            result = re.sub(rf"(?<!\w){re.escape(old)}(?!\w)", new, result, flags=re.I)
        return result

    def command(self, text):
        if self.key == "en":
            return text
        normalized = decimal_digits(text).strip()
        folded = normalized.casefold().rstrip(".!?")
        if folded in self.exact:
            return self.exact[folded]
        for prefix, command in self.prefixes:
            if folded.startswith(prefix.casefold() + " "):
                suffix = normalized[len(prefix):].strip()
                if command in {"remind me", "volume"}:
                    suffix = self.time_text(suffix)
                return command + " " + suffix
        return normalized

    def response(self, reply):
        if reply.key == "help" and self.key != "en":
            reply.text = "\n".join(self.data["examples"])
        elif reply.key in self.data["responses"]:
            reply.text = self.data["responses"][reply.key]
        return reply
