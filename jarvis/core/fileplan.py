"""File-change plans: preview -> typed "yes" -> revalidated, per-item execution.

Rules enforced here (pure Python; filesystem access is injected so tests can
use fakes and the Windows integration tests can use real disposable folders):
  * dry-run is the default: nothing changes, even with "yes".
  * only a typed exact "yes" for a fresh, unchanged plan applies it.
  * removal goes ONLY to the Recycle Bin via an injected trasher; if none is
    available the operation is refused. There is NO permanent-delete fallback.
  * overwrites are rejected; every item is revalidated right before it runs.
"""
from __future__ import annotations

import hashlib
import ntpath
import os
import stat
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable, Optional

from .safety import (PathEnv, TYPED_YES, check_mutation_path, confirmation_ok,
                     default_is_reparse)

OPS = ("recycle", "move", "rename")
PLAN_TTL_SECONDS = 300


@dataclass(frozen=True)
class PlanItem:
    op: str
    src: str
    dst: Optional[str]
    size: int
    mtime_ns: int
    identity: tuple = ()


@dataclass(frozen=True)
class FilePlan:
    op: str
    items: tuple
    refused: tuple               # ((path, reason), ...)
    created: datetime
    fingerprint: str
    dry_run: bool


@dataclass
class ItemResult:
    src: str
    ok: bool
    detail: str


@dataclass
class ExecResult:
    applied: bool
    reason: str
    items: list = field(default_factory=list)


def _fp(items: Iterable[PlanItem]) -> str:
    h = hashlib.sha256()
    for i in items:
        h.update(repr((i.op, i.src, i.dst, i.size, i.mtime_ns, i.identity)).encode("utf-8", "surrogatepass"))
    return h.hexdigest()


def _real_stat(path: str):
    st = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(st.st_mode):
        raise OSError("only regular files can be changed")
    return st.st_size, st.st_mtime_ns, st.st_dev, st.st_ino, st.st_ctime_ns


def _item(op, src, dst, stat_fn):
    size, mtime, *identity = stat_fn(src)
    return PlanItem(op, src, dst, size, mtime, tuple(identity))


def build_plan(op: str, entries: Iterable, approved_roots: list, env: PathEnv, *, now: datetime,
               dry_run: bool = True, realpath=os.path.realpath, is_reparse=default_is_reparse,
               stat_fn: Callable = _real_stat, exists_fn: Callable = os.path.lexists) -> FilePlan:
    """`entries` are paths (recycle) or (src, dst) pairs (move/rename)."""
    if op not in OPS:
        raise ValueError(f"unsupported operation: {op}")
    items, refused = [], []
    for e in entries:
        src, dst = (e, None) if isinstance(e, str) else (e[0], e[1])
        v = check_mutation_path(src, approved_roots, env, realpath=realpath, is_reparse=is_reparse)
        if not v.allowed:
            refused.append((src, v.reason))
            continue
        if op != "recycle":
            if not dst:
                refused.append((src, "no destination"))
                continue
            vd = check_mutation_path(dst, approved_roots, env, realpath=realpath, is_reparse=is_reparse)
            if not vd.allowed:
                refused.append((src, f"destination refused: {vd.reason}"))
                continue
            if exists_fn(dst):
                refused.append((src, "destination exists (overwrite refused)"))
                continue
        try:
            item = _item(op, src, dst, stat_fn)
        except OSError as exc:
            refused.append((src, f"cannot read item: {type(exc).__name__}"))
            continue
        items.append(item)
    return FilePlan(op, tuple(items), tuple(refused), now, _fp(items), dry_run)


def preview_text(plan: FilePlan) -> str:
    lines = [f"Plan: {plan.op} {len(plan.items)} item(s)" + ("  [DRY RUN - nothing will change]" if plan.dry_run else "")]
    for i in plan.items:
        lines.append(f"  {i.src}" + (f"  ->  {i.dst}" if i.dst else "  ->  Recycle Bin") + f"   ({i.size} bytes)")
    for path, why in plan.refused:
        lines.append(f"  REFUSED {path}: {why}")
    if plan.items and not plan.dry_run:
        lines.append('Type "yes" to apply this exact plan.')
    return "\n".join(lines)


def default_trasher() -> Optional[Callable[[str], None]]:
    """send2trash if importable, else None (=> removal refused, never permanent)."""
    if os.name != "nt":
        return None
    try:
        from send2trash.win.modern import send2trash
        return send2trash
    except (ImportError, OSError):
        return None


def execute_plan(plan: FilePlan, typed_text: str, source: str, approved_roots: list, env: PathEnv, *,
                 now: datetime, trasher: Optional[Callable[[str], None]] = None,
                 mover: Optional[Callable[[str, str], None]] = None,
                 realpath=os.path.realpath, is_reparse=default_is_reparse,
                 stat_fn: Callable = _real_stat, exists_fn: Callable = os.path.lexists) -> ExecResult:
    if not plan.items:
        return ExecResult(False, "nothing_to_do")
    if plan.dry_run:
        return ExecResult(False, "dry_run", [ItemResult(i.src, False, "dry run: not changed") for i in plan.items])
    if not confirmation_ok(TYPED_YES, source, typed_text):
        return ExecResult(False, "confirmation_required")
    if not 0 <= (now - plan.created).total_seconds() <= PLAN_TTL_SECONDS:
        return ExecResult(False, "stale_plan")
    # Whole-plan freshness check: anything changed since the preview -> refuse all.
    current = []
    try:
        for i in plan.items:
            current.append(_item(i.op, i.src, i.dst, stat_fn))
    except OSError:
        return ExecResult(False, "plan_changed")
    if _fp(current) != plan.fingerprint:
        return ExecResult(False, "plan_changed")
    if plan.op == "recycle":
        trasher = trasher or default_trasher()
        if trasher is None:
            return ExecResult(False, "recycle_unavailable")
    else:
        mover = mover or os.rename

    results = []
    for i in plan.items:
        v = check_mutation_path(i.src, approved_roots, env, realpath=realpath, is_reparse=is_reparse)
        if not v.allowed:
            results.append(ItemResult(i.src, False, f"refused: {v.reason}"))
            continue
        try:
            if _item(i.op, i.src, i.dst, stat_fn) != i:
                results.append(ItemResult(i.src, False, "changed after preview"))
                continue
            if plan.op == "recycle":
                trasher(i.src)
                if exists_fn(i.src):
                    results.append(ItemResult(i.src, False, "still present after recycle (not removed)"))
                else:
                    results.append(ItemResult(i.src, True, "moved to Recycle Bin"))
            else:
                if exists_fn(i.dst):
                    results.append(ItemResult(i.src, False, "destination exists (overwrite refused)"))
                    continue
                vd = check_mutation_path(i.dst, approved_roots, env, realpath=realpath, is_reparse=is_reparse)
                if not vd.allowed:
                    results.append(ItemResult(i.src, False, f"destination refused: {vd.reason}"))
                    continue
                mover(i.src, i.dst)
                results.append(ItemResult(i.src, True, f"{plan.op}d"))
        except Exception as exc:  # per-item report; never fall back to permanent deletion
            results.append(ItemResult(i.src, False, f"failed: {type(exc).__name__}"))
    return ExecResult(any(r.ok for r in results), "done", results)


def old_installers(downloads_dir: str, now: datetime, days: int = 90, scandir=os.scandir) -> list:
    """Regular .exe/.msi files directly inside Downloads, modified more than `days` ago.

    Non-recursive; skips directories, symlinks, junctions and other reparse points.
    """
    cutoff = now.timestamp() - days * 86400
    out = []
    with scandir(downloads_dir) as it:
        for e in it:
            try:
                if e.is_symlink() or not e.is_file(follow_symlinks=False):
                    continue
                if os.path.splitext(e.name)[1].lower() not in (".exe", ".msi"):
                    continue
                st = e.stat(follow_symlinks=False)
                if getattr(st, "st_file_attributes", 0) & 0x400:
                    continue
                if st.st_mtime < cutoff:
                    out.append(os.path.join(downloads_dir, e.name))
            except OSError:
                continue
    return sorted(out)
