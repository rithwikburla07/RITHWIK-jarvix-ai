"""Install the packaged Jarvix release for the current Windows user."""
from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE))
from jarvis.config.settings import read_json, write_json
from jarvis.maintenance.inventory import MANIFEST, checked, sha256

MARKER = ".jarvix-installing.json"


def payload(package):
    record = read_json(Path(package) / "PACKAGE-MANIFEST.json")
    if record.get("product") != "Jarvix Personal AI" or not isinstance(record.get("files"), dict):
        raise ValueError("The release package manifest is missing or invalid.")
    for name, digest in record["files"].items():
        if sha256(checked(package, name)) != digest:
            raise ValueError(f"Package file failed verification: {name}")
    return record["files"]


def copy_payload(package, destination, files):
    for name, digest in files.items():
        target = checked(destination, name)
        if target.exists():
            if sha256(target) != digest:
                raise ValueError(f"Existing installation file differs: {target}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(checked(package, name), target)


def install(destination, package, optional=False):
    files = payload(package)
    destination = destination.absolute()
    marker = checked(destination, MARKER)
    if destination.exists():
        if not marker.is_file() or read_json(marker) != files:
            raise ValueError("A different installation already exists. Use its maintenance utility first.")
    else:
        destination.mkdir(parents=True)
        write_json(marker, files)
    copy_payload(package, destination, files)
    environment = destination / ".venv"
    python = environment / "Scripts" / "python.exe"
    if not python.is_file():
        subprocess.run([sys.executable, "-I", "-m", "venv", str(environment)], check=True, timeout=180)
    args = [str(python), "-I", "-m", "pip", "install", "--no-compile", "--only-binary=:all:",
            "--disable-pip-version-check", "-r", str(destination / "requirements.txt")]
    if optional:
        args.extend(("-r", str(destination / "requirements-optional.txt")))
    subprocess.run(args, check=True, timeout=900)
    inventory = dict(files)
    for base, dirs, names in os.walk(environment, followlinks=False):
        for item in dirs + names:
            relative = (Path(base) / item).relative_to(destination).as_posix()
            checked(destination, relative)
        for name in names:
            path = Path(base) / name
            inventory[path.relative_to(destination).as_posix()] = sha256(path)
    write_json(destination / MANIFEST, {"product": "Jarvix Personal AI", "files": inventory})
    marker.unlink()
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--optional", action="store_true", help="Also install voice, Gmail and phone dependencies.")
    parser.add_argument("--check", action="store_true", help="Verify the release archive without installing.")
    args = parser.parse_args()
    if args.check:
        print(f"Verified {len(payload(PACKAGE))} package files.")
        return
    if os.name != "nt" or sys.version_info[:2] != (3, 12) or ctypes.windll.shell32.IsUserAnAdmin():
        parser.error("Use standard-user 64-bit Windows and Python 3.12.")
    dest = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Jarvix"
    try:
        result = install(dest, PACKAGE, args.optional)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Installation did not finish: {exc}\nRun the same command to resume an incomplete install.\n")
    print(f"Launch {result / 'Jarvix.exe'}")


if __name__ == "__main__":
    main()
