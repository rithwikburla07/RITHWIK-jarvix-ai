"""Where Jarvix keeps writable data: %LOCALAPPDATA%\\Jarvix (never inside the project)."""
from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("JARVIX_DATA_DIR")      # tests / portable use
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA")
    return (Path(base) / "Jarvix") if base else (Path.home() / ".jarvix")


def ensure_data_dir() -> Path:
    d = data_dir()
    d = d.absolute()
    if d.is_symlink() or d.resolve() != d:
        raise OSError("Jarvix data directory is redirected.")
    d.mkdir(parents=True, exist_ok=True)
    return d
