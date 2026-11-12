"""Verified download of small official Vosk models into the app profile."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from ..config.settings import read_json, write_json
from ..integrations.gmail import NoRedirect
from .backends import VoiceError, model_present

LIMIT = 200_000_000
EXTRACTED = 350_000_000
MODEL = re.compile(r"vosk-model-small-[A-Za-z0-9.-]{2,90}\Z")


def verify_request(url, checksum):
    value = urlsplit(url)
    name = PurePosixPath(value.path).stem
    if (value.scheme != "https" or value.netloc != "alphacephei.com" or value.query or value.fragment
            or value.path != "/vosk/models/" + name + ".zip" or not MODEL.fullmatch(name)):
        raise VoiceError("Use a small model ZIP from https://alphacephei.com/vosk/models/.")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", checksum):
        raise VoiceError("Enter the expected SHA-256 from a trusted source.")
    return name


def extract_model(archive, directory, name, cancel):
    digest_by_file = {}
    with zipfile.ZipFile(archive) as file:
        members = file.infolist()
        if len(members) > 500 or sum(member.file_size for member in members) > EXTRACTED:
            raise VoiceError("The model archive exceeds the extraction limits.")
        for member in members:
            parts = PurePosixPath(member.filename).parts
            if (not parts or parts[0] != name or any(part in {"..", ".", "/"} or
                not re.fullmatch(r"[A-Za-z0-9_.-]{1,150}", part) or part.endswith(".")
                or PureWindowsPath(part).is_reserved() for part in parts)
                or stat.S_ISLNK(member.external_attr >> 16)):
                raise VoiceError("The model ZIP contains an unsafe path.")
            target = directory.joinpath(*parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with file.open(member) as source, target.open("xb") as output:
                while True:
                    if cancel.is_set():
                        raise VoiceError("Model download cancelled.")
                    chunk = source.read(65_536)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
            digest_by_file["models/" + "/".join(parts)] = digest.hexdigest()
    return digest_by_file


def download(directory, url, checksum, cancel, progress):
    name = verify_request(url, checksum)
    if shutil.disk_usage(directory).free < LIMIT + EXTRACTED + 20_000_000:
        raise VoiceError("Free at least 570 MB before downloading a model.")
    root = Path(directory) / "models"
    if root.is_symlink() or root.resolve() != root:
        raise VoiceError("The app's model folder is redirected.")
    root.mkdir(exist_ok=True)
    destination = root / name
    if destination.exists():
        raise VoiceError("This model folder already exists. Choose it in Settings.")
    inventory = Path(directory) / "managed-models.json"
    try:
        saved = read_json(inventory, 2_000_000)
        if not isinstance(saved, dict):
            raise VoiceError("The managed model inventory is invalid.")
    except FileNotFoundError:
        saved = {}
    with tempfile.TemporaryDirectory(prefix=".vosk-", dir=root) as temp:
        staging = Path(temp)
        zip_path = staging / "model.zip"
        opener = build_opener(NoRedirect)
        digest, copied = hashlib.sha256(), 0
        request = Request(url, headers={"User-Agent": "Jarvix/1.1.7"})
        with opener.open(request, timeout=8) as response, zip_path.open("xb") as output:
            total = int(response.headers.get("Content-Length", "0"))
            if not 0 <= total <= LIMIT:
                raise VoiceError("Use a small Vosk model under 200 MB.")
            while not cancel.is_set():
                chunk = response.read(65_536)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > LIMIT:
                    raise VoiceError("The model ZIP exceeds 200 MB.")
                digest.update(chunk)
                output.write(chunk)
                progress(copied, total)
        if cancel.is_set():
            raise VoiceError("Model download cancelled.")
        if digest.hexdigest() != checksum.lower():
            raise VoiceError("SHA-256 check failed. The download was discarded.")
        known = extract_model(zip_path, staging, name, cancel)
        if not model_present(str(staging / name)):
            raise VoiceError("The ZIP does not contain a usable Vosk model.")
        os.rename(staging / name, destination)
        try:
            write_json(inventory, {**saved, **known})
        except OSError:
            os.rename(destination, staging / name)
            raise
    return destination
