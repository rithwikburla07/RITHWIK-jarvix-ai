"""Build the audited source and Windows launcher ZIP for Jarvix."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE = {".git", ".gradle", "build", "__pycache__", ".venv", ".pytest_cache"}
TOP = ("main.py", "README.md", "SETUP_MANUAL.md", "BUILD_STATUS.md", "PROGRESS.md",
       "PROJECT_TREE.txt", "check_requirements.ps1", "requirements.txt", "requirements-dev.txt",
       "requirements-optional.txt", "pytest.ini", ".gitignore")


def sources():
    files = {name: ROOT / name for name in TOP}
    allowed_data = {"apps.json", "contacts.sample.json", "settings.json", "workflows.json",
                    *(f"lang/{key}.json" for key in ("en", "hi", "ru", "uk", "pt", "tr", "it", "de"))}
    for name in ("jarvis", "tests", "android", "scripts", "data"):
        for path in (ROOT / name).rglob("*"):
            rel = path.relative_to(ROOT)
            if not path.is_file() or path.is_symlink() or any(part in EXCLUDE for part in rel.parts):
                continue
            if name == "data" and rel.as_posix().removeprefix("data/") not in allowed_data:
                continue
            if path.suffix.lower() in {".py", ".json", ".kt", ".kts", ".xml", ".properties", ".ps1", ".cs", ".md"}:
                files[rel.as_posix()] = path
    return files


def sha(data):
    return hashlib.sha256(data).hexdigest()


def build(output, binaries, original):
    files = sources()
    for name in ("Jarvix.exe", "jarvix-maintenance.exe"):
        path = binaries / name
        if path.read_bytes()[:2] != b"MZ":
            raise ValueError(f"Expected a compiled Windows executable: {path}")
        files[name] = path
    contents = {name: path.read_bytes() for name, path in sorted(files.items())}
    digests = {name: sha(data) for name, data in contents.items()}
    product = "Jarvix Personal AI"
    manifest = {"product": product, "files": digests}
    edits = {}
    staged_at = ROOT.stat().st_ctime
    for name, digest in digests.items():
        old = original / name
        if old.exists():
            if not old.is_file() or old.stat().st_mtime > staged_at:
                raise ValueError(f"Original changed while work was staged: {old}")
            before = sha(old.read_bytes())
        else:
            before = None
        if before != digest:
            edits[name] = {"old": before, "new": digest}
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
        archive.writestr("PACKAGE-MANIFEST.json", json.dumps(manifest, indent=2) + "\n")
        archive.writestr("UPDATE-MANIFEST.json", json.dumps({"product": product, "files": edits}, indent=2) + "\n")
    with ZipFile(output) as archive:
        if archive.testzip() is not None or any(sha(archive.read(name)) != digest for name, digest in digests.items()):
            raise OSError("Archive verification failed.")
    print(f"{len(contents)} verified payload files, {len(edits)} update entries, {output.stat().st_size:,} bytes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--binaries", required=True, type=Path)
    parser.add_argument("--original", required=True, type=Path)
    args = parser.parse_args()
    build(args.output, args.binaries, args.original)
