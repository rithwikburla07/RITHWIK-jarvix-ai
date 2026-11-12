"""User-confirmed uninstall. Unknown files, links and changed entries are retained."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..config.settings import read_json, write_json
from ..tasks.locking import SingleInstanceLock
from .inventory import MANIFEST, checked, identity, install_files, sha256

PROFILE_FILES = (
    "jarvix.db", "jarvix.db-wal", "jarvix.db-shm", "jarvix.db-journal",
    "settings.json", "approved-apps.json", "workflows.json", "contacts.json",
    "gmail-accounts.json", "phone-devices.json", "phone-identity.json",
    "jarvix.log", "jarvix.log.1", "jarvix.log.2", "jarvix.log.3",
    "diagnostics.txt", "feedback.txt", "calendar.ics", "managed-models.json",
)


@dataclass(frozen=True)
class Removal:
    root: Path
    name: str
    sha: str
    stat: tuple

    @property
    def path(self):
        return self.root / self.name


def build(root, profile, purge=False):
    root, profile = Path(root).absolute(), Path(profile).absolute()
    plan = []
    skipped = []
    install = install_files(root)
    install = {**install, MANIFEST: sha256(checked(root, MANIFEST))}
    for name, digest in install.items():
        file = checked(root, name)
        if not file.exists():
            continue
        snapshot = identity(file)
        if sha256(file) == digest:
            plan.append(Removal(root, name, digest, snapshot))
        else:
            skipped.append(str(file))
    if purge and profile.exists():
        for name in PROFILE_FILES:
            path = checked(profile, name)
            if not path.exists():
                continue
            if name == "jarvix.db":
                with path.open("rb") as handle:
                    if handle.read(16) != b"SQLite format 3\x00":
                        skipped.append(str(path))
                        continue
            plan.append(Removal(profile, name, sha256(path), identity(path)))
        if (profile / "managed-models.json").is_file():
            items = read_json(checked(profile, "managed-models.json"), 2_000_000)
            if not isinstance(items, dict) or len(items) > 3000:
                raise ValueError("Managed-model inventory is invalid.")
            for name, digest in items.items():
                if not name.startswith("models/vosk-model-small-") or "/" not in name[7:]:
                    raise ValueError("Managed-model inventory entry is invalid.")
                path = checked(profile, name)
                if path.is_file() and digest == sha256(path):
                    plan.append(Removal(profile, name, digest, identity(path)))
                elif path.exists():
                    skipped.append(str(path))
    return plan, skipped


def apply(plan):
    removed, retained, locked = [], [], []
    roots = {entry.root for entry in plan}
    parents = set()
    for entry in plan:
        path = entry.path
        try:
            path = checked(entry.root, entry.name)
            if identity(path) != entry.stat or sha256(path) != entry.sha:
                retained.append(str(path))
                continue
            path.unlink()
            removed.append(str(path))
            parents.update(p for p in path.parents if p.is_relative_to(entry.root) and p != entry.root)
        except PermissionError:
            locked.append(entry)
        except (FileNotFoundError, OSError, ValueError):
            retained.append(str(path))
    for folder in sorted(parents, key=lambda path: -len(path.parts)):
        try:
            folder.rmdir()
        except OSError:
            pass
    return removed, retained, locked


def delete_credentials(profile):
    import re
    path = checked(profile, "gmail-accounts.json")
    if not path.exists():
        return
    data = read_json(path)
    identifiers = data.get("accounts", {})
    if not identifiers:
        return
    from keyring.backends.Windows import WinVaultKeyring
    from keyring.errors import PasswordDeleteError
    for identifier in identifiers:
        if re.fullmatch(r"[a-f0-9]{32}", identifier):
            try:
                WinVaultKeyring().delete_password("Jarvix.Gmail", identifier)
            except PasswordDeleteError:
                pass


def run(root, profile, arguments, *, read=input, write=print):
    if arguments not in (["uninstall"], ["uninstall", "--purge"], ["uninstall", "--dry-run"],
                         ["uninstall", "--purge", "--dry-run"]):
        write("Use uninstall, uninstall --dry-run, or uninstall --purge.")
        return 2
    root, profile = Path(root).absolute(), Path(profile).absolute()
    purge = "--purge" in arguments
    try:
        plan, skipped = build(root, profile, purge)
        write("Jarvix Personal AI uninstall preview")
        write("Application files and listed Jarvix data will be removed." if purge else "Saved Jarvix data is kept.")
        for entry in plan:
            write(str(entry.path))
        if skipped:
            write("Changed or unrecognized files kept:\n" + "\n".join(skipped))
        if not plan or "--dry-run" in arguments:
            return 0
        if read('Type "yes" to apply this plan: ').strip().casefold() != "yes":
            write("Cancelled.")
            return 1
        lock = SingleInstanceLock(checked(profile, "jarvix.lock")) if profile.exists() else None
        if lock is not None and not lock.acquire():
            write("Exit Jarvix from the tray or console, then run the command again.")
            return 1
        try:
            if (plan, skipped) != build(root, profile, purge):
                write("The inventory changed. Run uninstall again to review the current files.")
                return 1
            if purge:
                delete_credentials(profile)
            finalizer = checked(root, "scripts/finalize_uninstall.py")
            finalizer_source = finalizer.read_bytes() if finalizer.exists() else None
            removed, retained, locked = apply(plan)
        finally:
            if lock is not None:
                lock.release()
        write(f"Removed {len(removed)} owned files.")
        if locked:
            write("Files still in use:\n" + "\n".join(str(item.path) for item in locked))
            deferred = [item for item in locked if item.root == root and
                        item.name in {"jarvix-maintenance.exe", ".venv/Scripts/python.exe"}]
            if deferred and finalizer_source:
                directory = Path(tempfile.mkdtemp(prefix="jarvix-uninstall-"))
                target = directory / "finalize.py"
                target.write_bytes(finalizer_source)
                write_json(directory / "remove.json", {"root": str(root), "files": [
                    {"name": item.name, "sha": item.sha, "stat": list(item.stat)} for item in deferred
                ]})
                subprocess.Popen([sys._base_executable, "-I", "-B", str(target)], shell=False,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                write(f"Launcher cleanup report will appear in {directory / 'report.txt'}.")
                locked = [item for item in locked if item not in deferred]
        if retained:
            write("Changed or inaccessible files kept:\n" + "\n".join(retained))
        return 1 if retained or locked else 0
    except (OSError, ValueError, EOFError, ImportError) as exc:
        write(f"Uninstall could not proceed: {type(exc).__name__}.")
        return 1
