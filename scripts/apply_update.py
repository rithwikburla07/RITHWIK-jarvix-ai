"""Apply a reviewed Jarvix update to an existing source project with backups."""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
from jarvis.config.paths import data_dir
from jarvis.config.settings import read_json
from jarvis.maintenance.inventory import checked, sha256
from jarvis.tasks.locking import SingleInstanceLock


def updates(package, root):
    record = read_json(package / "UPDATE-MANIFEST.json")
    if record.get("product") != "Jarvix Personal AI" or not isinstance(record.get("files"), dict):
        raise ValueError("Update manifest is invalid.")
    pending, conflicts = [], []
    for name, value in record["files"].items():
        source = checked(package, name)
        target = checked(root, name)
        if sha256(source) != value["new"]:
            raise ValueError(f"Update checksum failed for {name}.")
        actual = sha256(target) if target.exists() else None
        if actual == value["new"]:
            continue
        if actual != value["old"]:
            conflicts.append(name)
        else:
            pending.append((name, source, target))
    if conflicts:
        raise ValueError("Existing files changed after the update was prepared:\n" + "\n".join(conflicts))
    return pending


def apply_update(package, root, check=False):
    root = root.absolute()
    if not (root / "main.py").is_file() or not (root / "jarvis").is_dir():
        raise ValueError("Select the existing Jarvix source folder.")
    pending = updates(package, root)
    if check or not pending:
        return len(pending), None
    profile = data_dir()
    lock = SingleInstanceLock(profile / "jarvix.lock") if profile.is_dir() else None
    if lock is not None and not lock.acquire():
        raise ValueError("Exit the running Jarvix from its tray menu before applying the update.")
    backup = None
    try:
        pending = updates(package, root)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = Path(tempfile.mkdtemp(prefix=f"Jarvix-backup-{timestamp}-", dir=root))
        with tempfile.TemporaryDirectory(prefix="jarvix-update-", dir=root) as directory:
            staged = []
            for index, (name, source, target) in enumerate(pending):
                incoming = Path(directory) / str(index)
                shutil.copy2(source, incoming)
                saved = checked(backup, name)
                if target.exists():
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, saved)
                else:
                    saved = None
                staged.append((target, incoming, saved))
            applied = []
            try:
                for target, incoming, saved in staged:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(incoming, target)
                    applied.append((target, saved))
            except OSError:
                for target, saved in reversed(applied):
                    if saved is None:
                        target.unlink(missing_ok=True)
                    else:
                        shutil.copy2(saved, target)
                raise
    finally:
        if lock is not None:
            lock.release()
    return len(pending), backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        count, backup = apply_update(PACKAGE, args.project, args.check)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(f"{count} files {'ready to update' if args.check else 'updated'}.")
    if backup:
        print(f"Previous files saved to {backup}.")


if __name__ == "__main__":
    main()
