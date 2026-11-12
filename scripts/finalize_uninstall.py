"""Remove the verified maintenance launcher after its console process exits."""
import hashlib
import json
import os
import stat
import time
from pathlib import Path

folder = Path(__file__).resolve().parent
report = folder / "report.txt"
try:
    info = json.loads((folder / "remove.json").read_text(encoding="utf-8"))
    root = Path(info["root"])
    expected = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Jarvix"
    if root != expected or root.resolve() != root:
        raise ValueError("Unexpected application location.")
    for item in info["files"]:
        if item["name"] not in {"jarvix-maintenance.exe", ".venv/Scripts/python.exe"}:
            raise ValueError("Unexpected cleanup target.")
        path = root / item["name"]
        for _ in range(80):
            st = path.lstat()
            current = [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns]
            if (not stat.S_ISREG(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400 or
                    path.resolve() != path or current != item["stat"]):
                raise ValueError("The maintenance launcher changed.")
            with path.open("rb") as file:
                if hashlib.file_digest(file, "sha256").hexdigest() != item["sha"]:
                    raise ValueError("The maintenance launcher checksum changed.")
            try:
                path.unlink()
                break
            except PermissionError:
                time.sleep(.25)
        else:
            raise OSError("The maintenance launcher is still in use.")
    for folder in (root / '.venv' / 'Scripts', root / '.venv'):
        try:
            folder.rmdir()
        except OSError:
            pass
    try:
        root.rmdir()
    except OSError:
        pass
    report.write_text("Verified maintenance launcher removed.\n", encoding="utf-8")
except Exception as error:
    report.write_text(f"Launcher retained ({type(error).__name__}).\n", encoding="utf-8")
