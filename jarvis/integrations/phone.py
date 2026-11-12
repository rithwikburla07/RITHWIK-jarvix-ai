"""Explicit private-network TLS listener for the Android companion."""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import re
import ssl
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..config.settings import read_json, write_json
from ..core.intents import Reply
from .phone_protocol import CHUNK, LinkError, Pairing, Source
from .whatsapp import international_number
from .windows_secrets import protect

PRIVATE = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
BODY_LIMIT = 4096


def private_ip(text):
    try:
        ip = ipaddress.IPv4Address(text.strip())
    except (AttributeError, ValueError):
        raise LinkError("Enter this PC's private Wi-Fi IPv4 address.") from None
    if not any(ip in subnet for subnet in PRIVATE):
        raise LinkError("The companion accepts private Wi-Fi IPv4 addresses only.")
    return str(ip)


def tls_identity(directory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    path = Path(directory) / "phone-identity.json"
    try:
        record = read_json(path, 32_000)
        cert_bytes = record["cert"].encode("ascii")
        key_bytes = protect(base64.b64decode(record["key"], validate=True), decrypt=True)
        cert = x509.load_pem_x509_certificate(cert_bytes)
        if cert.not_valid_after_utc <= datetime.now(timezone.utc):
            raise LinkError("The companion TLS identity expired. Renew it before pairing again.")
    except FileNotFoundError:
        key = ec.generate_private_key(ec.SECP256R1())
        label = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Jarvix Personal AI")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(label).issuer_name(label)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=730))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .sign(key, hashes.SHA256()))
        cert_bytes = cert.public_bytes(serialization.Encoding.PEM)
        key_bytes = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption())
        write_json(path, {"cert": cert_bytes.decode("ascii"),
                          "key": base64.b64encode(protect(key_bytes)).decode("ascii")})
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with tempfile.TemporaryDirectory(prefix=".phone-tls-", dir=directory) as staging:
        pem, private = Path(staging) / "identity.pem", Path(staging) / "key.pem"
        pem.write_bytes(cert_bytes)
        private.write_bytes(key_bytes)
        context.load_cert_chain(pem, private)
    return context, cert.fingerprint(hashes.SHA256()).hex()


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        return

    def response(self, code, value):
        content = json.dumps(value).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= BODY_LIMIT or self.headers.get("Transfer-Encoding"):
                self.response(400, {"error": "Invalid request size."})
                return
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise LinkError("Invalid request.")
            if self.path == "/v1/download":
                self.download(data)
                return
            output = self.server.link.request(self.path, data)
            self.response(200, output)
        except LinkError as exc:
            self.response(403, {"error": str(exc)})
        except (ValueError, TypeError):
            self.response(400, {"error": "Invalid request."})
        except OSError:
            return

    def download(self, data):
        pairing = self.server.link.pairing
        device = pairing.authenticate(data)
        job = pairing.begin_stream(device, data.get("id"))
        sent, complete = 0, False
        try:
            file = job.source.open()
        except (OSError, LinkError):
            pairing.end_stream(job, False)
            self.response(409, {"error": "The source file changed."})
            return
        try:
            with file:
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(job.source.size))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                digest = hashlib.sha256()
                while sent < job.source.size and not job.stop.is_set():
                    chunk = file.read(min(CHUNK, job.source.size - sent))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    digest.update(chunk)
                    sent += len(chunk)
                    job.sent = sent
                complete = sent == job.source.size and digest.hexdigest() == job.source.sha256
        finally:
            pairing.end_stream(job, complete)


class TLSListener(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8
    allow_reuse_address = False

    def __init__(self, address, context, link):
        self.limit = threading.BoundedSemaphore(4)
        self.context, self.link = context, link
        super().__init__(address, RequestHandler)

    def process_request(self, request, client_address):
        if not self.limit.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.limit.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            request.settimeout(7)
            with self.context.wrap_socket(request, server_side=True) as secured:
                self.finish_request(secured, client_address)
        except OSError:
            request.close()
        finally:
            self.limit.release()


class PhoneLink:
    def __init__(self, directory, roots, contacts):
        self.directory, self.roots, self.contacts = Path(directory), roots, contacts
        self.pairing = Pairing(self.directory / "phone-devices.json")
        self.listener = self.thread = None
        self.address, self.pin = "", ""
        self.prepare_stop = threading.Event()
        self.progress = None
        self._lock = threading.RLock()

    def start(self, intent, _now):
        try:
            host = private_ip(intent.get("host", ""))
            port = intent.get("port", 8765)
            if type(port) is not int or not 1024 <= port <= 65535:
                raise LinkError("Choose a port from 1024 to 65535.")
            with self._lock:
                if self.listener:
                    raise LinkError("Stop the current companion listener first.")
                context, self.pin = tls_identity(self.directory)
                self.pairing.new_session()
                listener = TLSListener((host, port), context, self)
                self.listener = listener
                self.address = f"{host}:{port}"
                self.thread = threading.Thread(target=listener.serve_forever, kwargs={"poll_interval": .2},
                                               name="jarvix-phone", daemon=True)
                self.thread.start()
        except ImportError:
            return Reply("unavailable", "Install the optional companion TLS dependency first.", "phone.setup"), None
        except LinkError as exc:
            return Reply("error", str(exc), "phone.setup"), None
        except (OSError, ValueError):
            return Reply("error", "Could not start TLS on that private address and port.", "phone.setup"), None
        return Reply("done", f"Companion listener active on {self.address}.", "phone.status"), None

    def stop(self):
        self.prepare_stop.set()
        with self._lock:
            listener, thread = self.listener, self.thread
            self.listener = self.thread = None
            self.pairing.stop_all()
            if listener:
                listener.shutdown()
                listener.server_close()
                thread.join(2)

    def stop_intent(self, _intent, _now):
        self.stop()
        return Reply("done", "Companion listener stopped.", "phone.status"), None

    def invite(self, _intent, _now):
        with self._lock:
            if not self.listener:
                return Reply("unavailable", "Activate the companion listener first.", "phone.setup"), None
            invite = {"v": 1, "address": self.address, "pin": self.pin, "invite": self.pairing.new_invitation()}
        return Reply("info", "Pairing invitation expires after two minutes.", "phone.invite", {"invite": invite}), None

    def request(self, path, data):
        if path == "/v1/session":
            return {"session": self.pairing.session}
        if path == "/v1/pair":
            return self.pairing.pair(data.get("invite"), data.get("label"))
        device = self.pairing.authenticate(data)
        ident = data.get("id")
        if path == "/v1/poll":
            return {"jobs": self.pairing.poll(device)}
        if path == "/v1/accept":
            self.pairing.accept(device, ident)
        elif path == "/v1/finish":
            self.pairing.finish(device, ident, data.get("ok") is True, data.get("sha256", ""))
        elif path == "/v1/cancel":
            self.pairing.cancel(ident, device)
        else:
            raise LinkError("Unsupported companion request.")
        return {"ok": True}

    def view(self):
        return {**self.pairing.view(), "active": self.listener is not None, "progress": self.progress}

    def revoke(self, intent, _now):
        ident = intent.get("device")
        devices = {row["id"]: row["name"] for row in self.pairing.view()["devices"]}
        if ident not in devices:
            return Reply("error", "Select a paired phone.", "phone.setup"), None
        def apply(_confirmed):
            self.pairing.revoke(ident)
            return Reply("done", "Phone access revoked.", "phone.status")
        return Reply("confirm", f"Remove access for {devices[ident]}?\nType yes to confirm.", "phone.revoke"), apply

    def cancel(self, intent, _now):
        self.prepare_stop.set()
        self.pairing.cancel(intent.get("id"))
        return Reply("info", "Cancellation requested.", "phone.status"), None

    def action(self, intent, _now):
        active = self.listener
        if not active:
            return Reply("unavailable", "Activate the listener and pair a phone on the Android page.", "phone.setup"), None
        devices = self.pairing.view()["devices"]
        device = intent.get("device") or (devices[0]["id"] if len(devices) == 1 else None)
        if device not in {row["id"] for row in devices}:
            return Reply("unavailable", "Pair or select a phone on the Android page.", "phone.setup"), None
        action = intent.name.removeprefix("phone_")
        args, source = {}, None
        try:
            if action == "open_app":
                value = intent.get("app", "").strip()
                if not 1 <= len(value) <= 60 or not all(char.isprintable() for char in value):
                    raise LinkError("Enter an approved phone application name.")
                args["app"] = value
            elif action == "dial":
                value = intent.get("number") or intent.get("contact_query", "")
                saved = self.contacts.contacts.get(value.casefold())
                args["number"] = international_number(saved["number"] if saved else value)
            elif action == "timer":
                minutes = intent.get("minutes")
                if type(minutes) is not int or not 1 <= minutes <= 180:
                    raise LinkError("Use a timer from 1 to 180 minutes.")
                args["seconds"] = minutes * 60
            elif action == "alarm":
                hour, minute = intent.get("hour"), intent.get("minute")
                if type(hour) is not int or type(minute) is not int or not (0 <= hour < 24 and 0 <= minute < 60):
                    raise LinkError("Use a valid 24-hour alarm time.")
                args = {"hour": hour, "minute": minute}
            elif action == "transfer":
                self.prepare_stop = threading.Event()
                try:
                    source = Source.prepare(intent.get("path", ""), self.roots(), self.prepare_stop, self._progress)
                finally:
                    self.progress = None
                args = {"name": source.path.name, "size": source.size, "sha256": source.sha256, "sender": "Jarvix PC"}
            with self._lock:
                if self.listener is not active:
                    raise LinkError("The companion listener was stopped.")
                self.pairing.enqueue(device, action, args, source)
        except (OSError, ValueError) as exc:
            message = str(exc) if isinstance(exc, ValueError) else "The file could not be read."
            return Reply("error", message, "phone.error"), None
        return Reply("done", "Queued for the selected phone. Open the companion to receive it.", "phone.status"), None

    def _progress(self, done, total):
        self.progress = (done, total)
