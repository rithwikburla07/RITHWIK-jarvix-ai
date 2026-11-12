"""Explicit connectivity tests and local diagnostic export."""
from __future__ import annotations

import http.client
from datetime import datetime
from pathlib import Path

from ..core.intents import Reply
from ..core.redact import redact_text


def internet(_intent, _now):
    connection = http.client.HTTPSConnection("www.google.com", timeout=5)
    try:
        connection.request("HEAD", "/generate_204")
        response = connection.getresponse()
        if response.status == 204:
            return Reply("info", "Internet check reached the public HTTPS endpoint.", "diagnostics.internet"), None
        return Reply("info", f"Connectivity check returned HTTP {response.status}.", "diagnostics.internet"), None
    except (OSError, http.client.HTTPException):
        return Reply("unavailable", "Could not reach the public HTTPS endpoint.", "diagnostics.internet"), None
    finally:
        connection.close()


def write_report(directory, name, text):
    if name not in {"diagnostics.txt", "feedback.txt"}:
        raise ValueError("Unrecognized diagnostic report.")
    destination = Path(directory) / name
    if destination.is_symlink() or (destination.exists() and destination.resolve() != destination):
        raise OSError("Diagnostic path is redirected.")
    value = f"Jarvix v1.1.7 · {datetime.now():%Y-%m-%d %H:%M}\n" + redact_text(text)
    destination.write_text(value, encoding="utf-8")
    return destination
