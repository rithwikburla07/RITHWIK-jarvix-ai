"""Redaction for logs and diagnostics. Pure Python."""
from __future__ import annotations

import re

_I = re.I
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_TOKENS = [
    re.compile(r"\bya29\.[\w.-]+"), re.compile(r"\bGOCSPX-[\w-]+"), re.compile(r"\b1//[\w-]{20,}"),
    re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+"), re.compile(r"\bbearer\s+[\w.~+/=-]{8,}", _I),
    re.compile(r"\b(?:access[_-]?token|refresh[_-]?token|token|secret|password|api[_-]?key|client[_-]?secret)\s*[=:]\s*\S+", _I),
]
_PAIRING = re.compile(r"\b(?:pair(?:ing)?[ _-]?(?:code|token|key)|invite(?:[ _-]?code)?)\s*[=:]?\s*[\w-]{4,}", _I)
_LONG = re.compile(r"\b[A-Za-z0-9_-]{32,}\b")
_WA = re.compile(r"wa\.me/\+?\d+", _I)
_QS = re.compile(r"\b(text|to|su|body|subject)=[^&\s]+", _I)
_ISO = r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?"
_PHONE = re.compile(rf"(?P<iso>(?<![\w.]){_ISO}(?!\d))|(?P<ph>(?<![\w.])\+?\(?\d[\d\s().-]{{6,}}\d(?![\w]))")

SENSITIVE_SLOTS = {"message", "body", "subject", "phone", "number", "email", "to", "token",
                   "code", "pairing_code", "contact", "contact_query", "password", "secret"}


def _phone_sub(m: "re.Match") -> str:
    s = m.group(0)
    if m.group("iso") or sum(ch.isdigit() for ch in s) < 8:
        return s
    return "<phone>"


_MESSAGE_BODY = re.compile(r"(?im)^(\s*(?:message|body|subject)(?: [^:\r\n]{1,60})?:)[^\r\n]*")


def redact_text(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    for rx in _TOKENS:
        text = rx.sub("<token>", text)
    text = _PAIRING.sub("<pairing-code>", text)
    text = _EMAIL.sub("<email>", text)
    text = _WA.sub("wa.me/<phone>", text)
    text = _QS.sub(lambda m: f"{m.group(1)}=<redacted>", text)
    text = _PHONE.sub(_phone_sub, text)
    text = _LONG.sub("<token>", text)
    return _MESSAGE_BODY.sub(lambda match: match.group(1) + " <redacted>", text)


def redact_intent(name: str, slots: dict) -> str:
    parts = []
    for k, v in (slots or {}).items():
        if k in ("suggestions", "candidates"):
            continue
        parts.append(f"{k}=<redacted>" if k in SENSITIVE_SLOTS else f"{k}={redact_text(str(v))}")
    return f"{name}({', '.join(parts)})"
