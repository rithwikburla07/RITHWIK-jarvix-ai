"""Bounded inventories for installation, update and uninstall."""
from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path, PurePosixPath

from ..config.settings import read_json

MANIFEST = "INSTALL-MANIFEST.json"
MAX_FILES = 28_000
TOP = {
    "main.py", "README.md", "SETUP_MANUAL.md", "PROGRESS.md", "BUILD_STATUS.md",
    "requirements.txt", "requirements-dev.txt", "requirements-optional.txt", "pytest.ini",
    "check_requirements.ps1", "PROJECT_TREE.txt", ".gitignore", "Jarvix.exe",
    "jarvix-maintenance.exe",
}
APP_DIRS = {"jarvis", "data", "scripts", "tests", "android", ".venv"}


def sha256(path):
    with Path(path).open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def checked(root, name):
    if (not isinstance(name, str) or len(name) > 520 or not name or
            re.search(r'[:<>\\|?*\x00-\x1f]', name)):
        raise ValueError("Inventory contains an unsafe path.")
    parts = PurePosixPath(name).parts
    if (not parts or any(part in {"/", ".", ".."} or part.endswith((".", " ")) for part in parts) or
            name != "/".join(parts)):
        raise ValueError("Inventory contains an unsafe relative path.")
    root = Path(root).absolute()
    if root.resolve() != root or root.is_symlink() or (root.exists() and reparse(root)):
        raise ValueError("Jarvix root is redirected.")
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink() or (path.exists() and reparse(path)):
            raise ValueError("Inventory path contains a link or junction.")
    if path.resolve() != path or not path.is_relative_to(root):
        raise ValueError("Inventory path escaped the Jarvix directory.")
    return path


def reparse(path):
    st = path.lstat()
    return bool(getattr(st, "st_file_attributes", 0) & 0x400) or stat.S_ISLNK(st.st_mode)


def identity(path):
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400:
        raise ValueError("Inventory entry is not a regular file.")
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns


def install_files(root):
    path = checked(root, MANIFEST)
    record = read_json(path, 10_000_000)
    if not isinstance(record, dict):
        raise ValueError("Jarvix installation manifest is invalid.")
    files = record.get("files")
    if record.get("product") != "Jarvix Personal AI" or not isinstance(files, dict) or len(files) > MAX_FILES:
        raise ValueError("Jarvix installation manifest is invalid.")
    for name, digest in files.items():
        if not re.fullmatch(r"[a-f0-9]{64}", str(digest)):
            raise ValueError("Inventory checksum is invalid.")
        if name not in TOP and ("/" not in name or name.split("/", 1)[0] not in APP_DIRS):
            raise ValueError("Inventory includes a non-application file.")
        checked(root, name)
    return files
