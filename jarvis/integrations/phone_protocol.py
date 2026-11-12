"""Pairing, replay protection, device permissions and bounded transfer state."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import stat
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..config.settings import read_json, write_json
from ..core.safety import PathEnv, check_mutation_path

MAX_FILE = 2_000_000_000
CHUNK = 64 * 1024
PAIR_TTL = 120
AUTH_WINDOW = 45
ACTIONS = {"ring", "dial", "open_app", "timer", "alarm", "transfer"}
TOKEN = re.compile(r"[A-Za-z0-9_-]{24,80}\Z")
IDENTIFIER = re.compile(r"[a-f0-9]{32}\Z")


class LinkError(ValueError):
    pass


def hash_token(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def fingerprint(st):
    if not stat.S_ISREG(st.st_mode) or not 0 <= st.st_size <= MAX_FILE:
        raise LinkError("Choose a regular file of at most 2,000,000,000 bytes.")
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns


@dataclass
class Source:
    path: Path
    roots: list
    identity: tuple
    sha256: str

    @property
    def size(self):
        return self.identity[2]

    def open(self):
        if not check_mutation_path(str(self.path), self.roots, PathEnv.from_environ()).allowed:
            raise LinkError("The file left an approved folder.")
        file = self.path.open("rb")
        try:
            if fingerprint(os.fstat(file.fileno())) != self.identity:
                raise LinkError("The selected file changed.")
        except BaseException:
            file.close()
            raise
        return file

    @classmethod
    def prepare(cls, filename, roots, stop, progress):
        path = Path(filename)
        env = PathEnv.from_environ()
        if not check_mutation_path(str(path), roots, env).allowed:
            raise LinkError("Choose a file in an approved local folder.")
        identity = fingerprint(path.stat(follow_symlinks=False))
        source = cls(path, roots, identity, "")
        digest, count = hashlib.sha256(), 0
        with source.open() as file:
            while not stop.is_set():
                data = file.read(CHUNK)
                if not data:
                    break
                count += len(data)
                if count > source.size:
                    raise LinkError("The selected file changed.")
                digest.update(data)
                progress(count, source.size)
            if stop.is_set() or count != source.size or fingerprint(os.fstat(file.fileno())) != identity:
                raise LinkError("Transfer preparation cancelled or the file changed.")
        source.sha256 = digest.hexdigest()
        return source


@dataclass
class Job:
    device: str
    action: str
    args: dict
    created: float
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "offered"
    source: Source | None = None
    stop: threading.Event = field(default_factory=threading.Event)
    attempts: int = 0
    sent: int = 0

    def public(self):
        return {"id": self.id, "action": self.action, "args": self.args,
                "status": self.status, "sent": self.sent}


class Pairing:
    def __init__(self, path=None, clock=time.time):
        self.path = Path(path) if path else None
        self.clock, self.lock = clock, threading.RLock()
        self.devices, self.nonces, self.jobs, self.seen = {}, {}, {}, {}
        self.invitation = None
        self.session = secrets.token_urlsafe(24)
        if self.path:
            try:
                values = read_json(self.path)
                if isinstance(values, dict):
                    for ident, device in list(values.items())[:8]:
                        if (IDENTIFIER.fullmatch(ident) and isinstance(device, dict) and
                                re.fullmatch(r"[a-f0-9]{64}", str(device.get("hash", "")))):
                            label = str(device.get("label", "Android"))[:48]
                            self.devices[ident] = {"hash": device["hash"], "label": label}
            except FileNotFoundError:
                pass

    def new_session(self):
        with self.lock:
            self.session = secrets.token_urlsafe(24)
            self.nonces.clear()

    def new_invitation(self):
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.invitation = hash_token(token), self.clock() + PAIR_TTL
        return token

    def pair(self, token, label):
        if not isinstance(token, str) or not TOKEN.fullmatch(token) or not isinstance(label, str):
            raise LinkError("Invalid invitation.")
        label = "".join(c for c in label if c.isprintable()).strip()[:48] or "Android"
        with self.lock:
            if not self.invitation:
                raise LinkError("Invitation expired or used.")
            expected, expiry = self.invitation
            if self.clock() > expiry or not hmac.compare_digest(expected, hash_token(token)):
                raise LinkError("Invitation expired or used.")
            if len(self.devices) >= 8:
                raise LinkError("Remove a paired phone before adding another.")
            device, secret = uuid.uuid4().hex, secrets.token_urlsafe(40)
            devices = {**self.devices, device: {"hash": hash_token(secret), "label": label}}
            self._save(devices)
            self.invitation = None
            self.seen[device] = self.clock()
            return {"device": device, "token": secret}

    def _save(self, devices):
        if self.path:
            write_json(self.path, devices)
        self.devices = devices

    def authenticate(self, request):
        device, token, nonce, stamp = (request.get(key) for key in ("device", "token", "nonce", "time"))
        if (not all(isinstance(value, str) for value in (device, token, nonce)) or
                not IDENTIFIER.fullmatch(device) or not TOKEN.fullmatch(token) or
                not TOKEN.fullmatch(nonce) or type(stamp) is not int):
            raise LinkError("Invalid device credentials.")
        session = request.get("session")
        if not isinstance(session, str) or not hmac.compare_digest(session, self.session):
            raise LinkError("Companion session changed. Reconnect.")
        now = self.clock()
        if abs(now - stamp) > AUTH_WINDOW:
            raise LinkError("Phone time differs from this PC.")
        with self.lock:
            approved = self.devices.get(device)
            if not approved or not hmac.compare_digest(approved["hash"], hash_token(token)):
                raise LinkError("Device credential rejected.")
            known = self.nonces.setdefault(device, {})
            for key in [key for key, expiry in known.items() if expiry < now]:
                del known[key]
            if nonce in known or len(known) >= 300:
                raise LinkError("Request replayed or rate limited.")
            known[nonce] = now + AUTH_WINDOW * 2
            self.seen[device] = now
            return device

    def _expire(self):
        now = self.clock()
        for job in self.jobs.values():
            limit = 1800 if job.action == "transfer" else 180
            if job.status in {"offered", "approved", "streamed", "streaming"} and now - job.created > limit:
                job.status = "expired"
                job.stop.set()

    def enqueue(self, device, action, args, source=None):
        with self.lock:
            self._expire()
            if action not in ACTIONS or device not in self.devices or (action == "transfer") != bool(source):
                raise LinkError("Select a paired phone and a supported action.")
            if sum(job.device == device and job.status in {"offered", "approved", "streamed", "streaming"}
                   for job in self.jobs.values()) >= 6:
                raise LinkError("This phone already has six pending actions.")
            if len(self.jobs) >= 90:
                for ident, old in list(self.jobs.items()):
                    if old.status in {"complete", "cancelled", "failed", "expired"}:
                        del self.jobs[ident]
                    if len(self.jobs) < 90:
                        break
            if len(self.jobs) >= 90:
                raise LinkError("The phone queue is full.")
            job = Job(device, action, args, self.clock(), source=source)
            self.jobs[job.id] = job
            return job.id

    def poll(self, device):
        with self.lock:
            self._expire()
            return [job.public() for job in self.jobs.values() if job.device == device and job.status == "offered"]

    def job(self, device, ident):
        job = self.jobs.get(ident) if isinstance(ident, str) else None
        if job is None or job.device != device or job.stop.is_set():
            raise LinkError("This action expired or was cancelled.")
        return job

    def accept(self, device, ident):
        with self.lock:
            self._expire()
            job = self.job(device, ident)
            if job.action != "transfer" or job.status != "offered":
                raise LinkError("This transfer is no longer available for approval.")
            job.status = "approved"

    def begin_stream(self, device, ident):
        with self.lock:
            self._expire()
            job = self.job(device, ident)
            if job.action != "transfer" or job.status not in {"approved", "streamed"} or job.attempts >= 3:
                raise LinkError("The phone must approve this transfer and have a retry remaining.")
            job.status, job.sent = "streaming", 0
            job.attempts += 1
            return job

    def end_stream(self, job, complete):
        with self.lock:
            if not job.stop.is_set() and job.status == "streaming":
                job.status = "streamed" if complete else "approved" if job.attempts < 3 else "failed"

    def finish(self, device, ident, ok, sha256=""):
        with self.lock:
            job = self.job(device, ident)
            if job.action == "transfer":
                if job.status not in {"approved", "streamed"}:
                    raise LinkError("This transfer is not ready for confirmation.")
                if ok and (job.status != "streamed" or not isinstance(sha256, str) or
                           not hmac.compare_digest(job.source.sha256, sha256)):
                    raise LinkError("File integrity confirmation failed.")
            elif job.status != "offered":
                raise LinkError("This action was already answered.")
            job.status = "complete" if ok else "failed"

    def cancel(self, ident, device=None):
        with self.lock:
            job = self.jobs.get(ident)
            if job is None or (device and job.device != device) or job.status in {"complete", "failed", "cancelled", "expired"}:
                return False
            job.stop.set()
            job.status = "cancelled"
            return True

    def stop_all(self):
        with self.lock:
            self.invitation = None
            for ident in list(self.jobs):
                self.cancel(ident)

    def revoke(self, ident):
        with self.lock:
            if ident not in self.devices:
                raise LinkError("Select a paired phone.")
            self._save({key: value for key, value in self.devices.items() if key != ident})
            self.nonces.pop(ident, None)
            self.seen.pop(ident, None)
            for job in self.jobs.values():
                if job.device == ident:
                    self.cancel(job.id)

    def view(self):
        with self.lock:
            self._expire()
            return {
                "devices": [{"id": ident, "name": value["label"],
                             "online": self.clock() - self.seen.get(ident, 0) < 15}
                            for ident, value in self.devices.items()],
                "jobs": [job.public() for job in list(self.jobs.values())[-40:]],
            }
