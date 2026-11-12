"""Optional loopback-only Brain. Its response is display data."""
from __future__ import annotations

import http.client
import ipaddress
import json
import re
from urllib.parse import urlsplit

from ..core.intents import Reply

MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9/_.:-]{0,79}\Z")
PATHS = {"": "/api/generate", "/api/generate": "/api/generate",
         "/v1/chat/completions": "/v1/chat/completions"}


def endpoint(value):
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    if host == "localhost":
        host = "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("Use a literal loopback address, such as 127.0.0.1.") from exc
    if (not address.is_loopback or parsed.scheme != "http" or parsed.path not in PATHS
            or parsed.query or parsed.fragment or parsed.username is not None or parsed.password is not None):
        raise ValueError("Use an HTTP loopback endpoint with a supported API path.")
    return str(address), parsed.port or 11434, PATHS[parsed.path]


def answer(url, model, prompt):
    host, port, path = endpoint(url)
    if not MODEL.fullmatch(model):
        raise ValueError("Enter the installed local model name.")
    prompt = prompt.strip()
    if not 1 <= len(prompt) <= 900:
        raise ValueError("Enter a question up to 900 characters.")
    system = "Answer briefly. You can suggest a command as text, but you cannot execute any action."
    if path == "/api/generate":
        payload = {"model": model, "prompt": prompt, "system": system, "stream": False, "keep_alive": 0,
                   "options": {"num_ctx": 1024, "num_predict": 200}}
    else:
        payload = {"model": model, "stream": False, "max_tokens": 200,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    connection = http.client.HTTPConnection(host, port, timeout=8)
    try:
        connection.request("POST", path, json.dumps(payload).encode(), {"Content-Type": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f"The local model server returned HTTP {response.status}.")
        raw = response.read(65_537)
        if len(raw) > 65_536:
            raise ValueError("The local model response exceeded the size limit.")
        body = json.loads(raw)
        text = body.get("response") if path == "/api/generate" else body["choices"][0]["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("The local model returned an empty answer.")
        return text.strip()[:3_000]
    finally:
        connection.close()


class Brain:
    def __init__(self, settings):
        self.settings = settings

    def handle(self, intent, _now):
        if not self.settings.get("brain_enabled"):
            return Reply("unavailable", "Enable and configure the local Brain in Settings.", "brain.off"), None
        try:
            result = answer(self.settings.get("brain_endpoint"), self.settings.get("brain_model"),
                            intent.get("prompt", ""))
        except ValueError as exc:
            return Reply("error", str(exc), "brain.error"), None
        except (OSError, http.client.HTTPException, KeyError, TypeError, IndexError):
            return Reply("error", "The local model did not respond. Check that it is running.", "brain.error"), None
        return Reply("info", result, "brain.answer"), None
