"""Read-only file and folder search inside approved roots only.

Never follows symlinks, junctions or other reparse points, and never leaves a root.
Bounded by depth, visited entries, and result count.
"""
from __future__ import annotations

import ntpath
import os
from dataclasses import dataclass

from ..core.safety import PathEnv, validate_approved_root

MAX_RESULTS = 50
MAX_VISITED = 5000
MAX_DEPTH = 6
REPARSE = 0x400


@dataclass(frozen=True)
class Hit:
    path: str
    is_dir: bool


def approved_roots(env: dict, extra=()) -> list:
    """Desktop, Documents and Downloads under the profile, plus explicitly enabled extras."""
    profile = env.get("USERPROFILE")
    if not profile:
        return []
    pe = PathEnv(profile=profile, users_dir=ntpath.dirname(profile.rstrip("\\")) or "C:\\Users")
    cands = [ntpath.join(profile, "Desktop"), ntpath.join(profile, "Documents"),
             ntpath.join(profile, "Downloads"), *extra]
    return [c for c in cands if validate_approved_root(c, pe).allowed]


def _list_real(path: str) -> list:
    with os.scandir(path) as it:
        yield from it


def _is_reparse(entry) -> bool:
    st = entry.stat(follow_symlinks=False)
    return bool(getattr(st, "st_file_attributes", 0) & REPARSE)


def search(query: str, roots, *, lister=_list_real, max_results: int = MAX_RESULTS,
           max_depth: int = MAX_DEPTH, max_visited: int = MAX_VISITED) -> list:
    q = (query or "").strip().casefold()
    if not 2 <= len(q) <= 100:
        raise ValueError("search text must be 2 to 100 characters")
    hits, visited = [], 0
    stack = [(r, 0) for r in reversed(list(roots))]
    while stack and len(hits) < max_results and visited < max_visited:
        folder, depth = stack.pop()
        try:
            entries = lister(folder)
            for e in entries:
                if visited >= max_visited or len(hits) >= max_results:
                    break
                visited += 1
                try:
                    if e.is_symlink() or _is_reparse(e):
                        continue
                    is_dir = e.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if q in e.name.casefold():
                    hits.append(Hit(e.path, is_dir))
                if is_dir and depth + 1 < max_depth:
                    stack.append((e.path, depth + 1))
        except OSError:
            continue
    return hits
