"""Read-only Gmail REST access and separately confirmed browser composition."""
from __future__ import annotations

import base64
import hmac
import importlib.util
import json
import re
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..config.settings import read_json, write_json
from ..core.intents import Reply
from ..core.safety import PathEnv, check_mutation_path

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
API = "https://gmail.googleapis.com/gmail/v1/users/me/"
SECRET_SERVICE = "Jarvix.Gmail"
_GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
_GOOGLE_AUTH = {"https://accounts.google.com/o/oauth2/auth", "https://accounts.google.com/o/oauth2/v2/auth"}
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_{|}~-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\Z")
MESSAGE_ID = re.compile(r"[A-Za-z0-9_-]{1,100}\Z")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def compose_url(recipient, subject, body, account=""):
    if not isinstance(recipient, str) or not EMAIL.fullmatch(recipient):
        raise ValueError("Enter one valid Gmail recipient address.")
    if not isinstance(subject, str) or len(subject) > 250 or any(ord(c) < 32 for c in subject):
        raise ValueError("Use a subject up to 250 characters on one line.")
    if not isinstance(body, str) or len(body) > 2500 or "\x00" in body:
        raise ValueError("Use a body up to 2,500 characters.")
    params = {"view": "cm", "fs": "1", "to": recipient, "su": subject, "body": body}
    if account and EMAIL.fullmatch(account):
        params["authuser"] = account
    return "https://mail.google.com/mail/?" + urlencode(params)


def api_get(access_token, endpoint, parameters=None):
    url = API + endpoint + ("?" + urlencode(parameters, doseq=True) if parameters else "")
    opener = build_opener(NoRedirect)
    request = Request(url, headers={"Authorization": "Bearer " + access_token, "Accept": "application/json"})
    with opener.open(request, timeout=6) as response:
        content = response.read(2_000_001)
        if len(content) > 2_000_000:
            raise ValueError("The Gmail response exceeded the preview size limit.")
        return json.loads(content)


def vault():
    from keyring.backends.Windows import WinVaultKeyring
    return WinVaultKeyring()


def installed_credentials(path):
    path = Path(path)
    if not path.is_absolute() or path.suffix.casefold() != ".json":
        raise ValueError("Select a local Desktop OAuth client JSON file.")
    if not check_mutation_path(str(path), [str(path.parent)], PathEnv.from_environ()).allowed:
        raise ValueError("Select an OAuth file in an approved local directory.")
    obj = read_json(path, 32_000)
    data = obj.get("installed", {})
    if (data.get("token_uri") != _GOOGLE_TOKEN or data.get("auth_uri") not in _GOOGLE_AUTH
            or not str(data.get("client_id", "")).endswith(".apps.googleusercontent.com")):
        raise ValueError("Use a Google Desktop app OAuth client JSON file.")
    return obj


def authorize(config, cancelled, open_browser):
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_config(config, [SCOPE], autogenerate_code_verifier=True)
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_):
            return

        def setup(self):
            self.request.settimeout(1.5)
            super().setup()

        def do_GET(self):
            valid = False
            if len(self.path) <= 4096 and not cancelled.is_set():
                request = urlsplit(self.path)
                params = parse_qs(request.query, max_num_fields=10)
                state = params.get("state", [""])[0]
                valid = request.path == "/callback" and hmac.compare_digest(state.encode(), expected.encode())
                if valid:
                    result["code"] = params.get("code", [""])[0]
            self.send_response(200 if valid else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b"Return to Jarvix." if valid else b"Invalid Jarvix authorization response.")

    with HTTPServer(("127.0.0.1", 0), Callback) as listener:
        listener.timeout = .25
        flow.redirect_uri = f"http://127.0.0.1:{listener.server_port}/callback"
        address, expected = flow.authorization_url(access_type="offline", prompt="select_account consent")
        if not open_browser(address):
            raise ValueError("The browser could not open Google sign-in.")
        deadline = time.monotonic() + 90
        while "code" not in result and time.monotonic() < deadline and not cancelled.is_set():
            listener.handle_request()
    if cancelled.is_set() or not result.get("code"):
        raise ValueError("Gmail sign-in cancelled or expired.")
    flow.fetch_token(code=result["code"], timeout=8)
    credentials = flow.credentials
    if cancelled.is_set() or not credentials.refresh_token or not credentials.has_scopes([SCOPE]):
        raise ValueError("Gmail did not grant the required read-only permission.")
    return credentials


def header(payload, name, fallback=""):
    return next((value.get("value", fallback) for value in payload.get("headers", [])
                 if value.get("name", "").casefold() == name.casefold()), fallback)


def plain_message(payload, depth=0):
    if depth > 8:
        return ""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")[:7000]
    for part in payload.get("parts", [])[:20]:
        value = plain_message(part, depth + 1)
        if value:
            return value
    return ""


class Gmail:
    def __init__(self, path, open_url, get=api_get, authorize_account=authorize, credentials_vault=vault):
        self.path, self.open_url, self.get = Path(path), open_url, get
        self.authorize_account, self.credentials_vault = authorize_account, credentials_vault
        self.accounts = {}
        self.selected = ""
        self.last_messages = []
        self.cancel_connect = threading.Event()
        self.problem = ""
        try:
            record = read_json(self.path)
            values = record.get("accounts", {})
            self.accounts = {key: value for key, value in values.items()
                             if re.fullmatch(r"[a-f0-9]{32}", key) and isinstance(value, str) and EMAIL.fullmatch(value)}
            self.selected = record.get("selected", "")
            if self.selected not in self.accounts:
                self.selected = next(iter(self.accounts), "")
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError):
            self.problem = "Saved Gmail accounts could not be read."

    def _save(self, accounts, selected):
        write_json(self.path, {"accounts": accounts, "selected": selected})
        self.accounts, self.selected = accounts, selected
        self.last_messages = []
        self.problem = ""

    def status(self):
        missing = [package for package in ("google_auth_oauthlib", "keyring") if not importlib.util.find_spec(package)]
        if missing:
            return "Gmail reading requires the optional Google OAuth and Windows credential packages."
        return self.problem or (f"{len(self.accounts)} Gmail account(s) stored" if self.accounts else "Gmail disconnected")

    def stop(self):
        self.cancel_connect.set()

    def connect(self, intent, _now):
        cancelled = self.cancel_connect = threading.Event()
        try:
            secrets = installed_credentials(intent.get("path", ""))
            keychain = self.credentials_vault()
            credentials = self.authorize_account(secrets, cancelled, self.open_url)
            account = self.get(credentials.token, "profile").get("emailAddress", "")
            if cancelled.is_set() or not EMAIL.fullmatch(account):
                raise ValueError("Gmail did not return a valid account.")
            key = next((key for key, value in self.accounts.items() if value.casefold() == account.casefold()),
                       uuid.uuid4().hex)
            if key not in self.accounts and len(self.accounts) >= 10:
                raise ValueError("Disconnect an account before adding an eleventh.")
            keychain.set_password(SECRET_SERVICE, key, credentials.to_json())
            self._save({**self.accounts, key: account}, key)
        except ImportError:
            return Reply("unavailable", self.status(), "gmail.setup"), None
        except ValueError as exc:
            if str(exc) in {"Select a local Desktop OAuth client JSON file.",
                            "Select an OAuth file in an approved local directory.",
                            "Use a Google Desktop app OAuth client JSON file."}:
                return Reply("error", str(exc), "gmail.connect_failed"), None
            return Reply("error", "Gmail authorization could not be completed.", "gmail.connect_failed"), None
        except Exception as exc:
            return Reply("error", f"Gmail authorization ended ({type(exc).__name__}).", "gmail.connect_failed"), None
        return Reply("done", "Gmail account connected.", "gmail.accounts"), None

    def choose(self, intent, _now):
        key = intent.get("id")
        if key not in self.accounts:
            return Reply("error", "Choose a connected Gmail account.", "gmail.account"), None
        self._save(dict(self.accounts), key)
        return Reply("done", "Gmail account selected.", "gmail.accounts"), None

    def disconnect(self, intent, _now):
        key = intent.get("id") or self.selected
        if key not in self.accounts:
            return Reply("info", "No Gmail account is selected.", "gmail.setup"), None

        def remove(_confirmed_at):
            from keyring.errors import PasswordDeleteError
            try:
                self.credentials_vault().delete_password(SECRET_SERVICE, key)
            except PasswordDeleteError:
                pass
            accounts = {ident: name for ident, name in self.accounts.items() if ident != key}
            self._save(accounts, self.selected if self.selected in accounts else next(iter(accounts), ""))
            return Reply("done", "Removed this account and its stored credentials.", "gmail.accounts")

        return Reply("confirm", f"Remove the stored Gmail connection for {self.accounts[key]}?\nType yes to confirm.",
                     "gmail.disconnect"), remove

    def _token(self):
        from google.auth.transport.requests import Request as AuthRequest
        from google.oauth2.credentials import Credentials

        keychain = self.credentials_vault()
        stored = keychain.get_password(SECRET_SERVICE, self.selected)
        if not stored:
            raise ValueError("Reconnect the selected Gmail account.")
        record = json.loads(stored)
        if record.get("token_uri") != _GOOGLE_TOKEN or record.get("scopes") != [SCOPE]:
            raise ValueError("Reconnect the selected Gmail account.")
        credentials = Credentials.from_authorized_user_info(record, [SCOPE])
        if not credentials.valid:
            transport = AuthRequest()
            credentials.refresh(lambda *args, **kwargs: transport(*args, **{**kwargs, "timeout": 6}))
            keychain.set_password(SECRET_SERVICE, self.selected, credentials.to_json())
        return credentials.token

    def read(self, intent, _now):
        if not self.selected:
            return Reply("unavailable", "Connect a Gmail account on the Gmail page to read messages.", "gmail.setup"), None
        try:
            token = self._token()
            if intent.name == "gmail_unread":
                data = self.get(token, "labels/INBOX")
                return Reply("info", f"Unread inbox messages: {int(data['messagesUnread'])}.", "gmail.unread"), None
            if intent.name == "gmail_read":
                index = intent.get("index")
                if type(index) is not int or not 0 <= index < len(self.last_messages):
                    return Reply("error", "Select a current Gmail result.", "gmail.selection"), None
                endpoint, subject = self.last_messages[index]
                data = self.get(token, endpoint, {"format": "full"})
                message = data.get("message", data)
                body = plain_message(message.get("payload", {})) or message.get("snippet", "")
                preview = f"{subject}\n\n{body[:7000]}"
                return Reply("info", "Email preview ready.", "gmail.preview", {"preview": preview}), None
            drafts = intent.name == "gmail_drafts"
            key = "drafts" if drafts else "messages"
            params = {"maxResults": 5}
            if not drafts:
                params["labelIds"] = "INBOX"
            result = self.get(token, key, params)
            rows = []
            for record in result.get(key, [])[:5]:
                identifier = record.get("id", "")
                if not MESSAGE_ID.fullmatch(identifier):
                    continue
                endpoint = key + "/" + quote(identifier, safe="")
                data = self.get(token, endpoint, {"format": "metadata"})
                subject = header(data.get("message", data).get("payload", {}), "Subject", "(No subject)")
                rows.append((endpoint, str(subject)[:250]))
            self.last_messages = rows
            return Reply("info", f"Loaded {len(rows)} {'drafts' if drafts else 'inbox messages'}.",
                         "gmail.list", {"subjects": [subject for _, subject in rows]}), None
        except HTTPError as exc:
            return Reply("error", f"Gmail rejected the request (HTTP {exc.code}).", "gmail.read_error"), None
        except ImportError:
            return Reply("unavailable", self.status(), "gmail.setup"), None
        except Exception as exc:
            return Reply("error", f"Gmail could not be read ({type(exc).__name__}).", "gmail.read_error"), None

    def compose(self, intent, _now):
        recipient = intent.get("to", "").strip()
        subject, body = intent.get("subject", ""), intent.get("body", "")
        try:
            url = compose_url(recipient, subject, body, self.accounts.get(self.selected, ""))
        except ValueError as exc:
            return Reply("error", str(exc), "gmail.compose_error"), None

        def open_compose(_confirmed_at):
            if not self.open_url(url):
                return Reply("error", "The browser could not open Gmail.", "gmail.failed")
            return Reply("done", "Gmail compose opened. Press Send in Gmail.", "gmail.opened")

        preview = f"Gmail recipient: {recipient}\nSubject: {subject}\nMessage: {body}\nType yes to open Gmail compose."
        return Reply("confirm", preview, "gmail.compose_preview"), open_compose
