"""Action policy + Windows path safety. Pure Python, no Qt, no side effects.

Path logic uses `ntpath` so it behaves identically on any OS (tests included).
Filesystem access (realpath, reparse detection) is injectable and fails closed.
"""
from __future__ import annotations

import ntpath
import os
import stat
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

# ----------------------------------------------------------------- policy
DIRECT, CONFIRM, TYPED_YES, REFUSE = "direct", "confirm", "typed_yes", "refuse"

_DIRECT = {
    "open_app", "open_folder", "open_site", "open_url", "set_volume", "volume_up",
    "volume_down", "mute", "unmute", "show_tasks", "create_reminder", "create_task",
    "complete_item", "snooze", "reschedule", "run_workflow", "create_workflow",
    "youtube_search", "web_search", "file_search", "music_play", "music_pause",
    "music_resume", "music_stop", "music_next", "music_previous", "sysinfo",
    "gmail_unread", "gmail_latest", "gmail_drafts", "phone_open_app", "phone_ring",
    "phone_dial", "whatsapp_unsupported", "jarvix_pause", "jarvix_resume",
    "jarvix_exit", "help", "cancel", "unknown", "reveal_search_hit", "discover_apps", "app_alias",
    "workflow_save", "power_list", "task_calendar", "music_scan", "music_select", "gmail_read", "gmail_connect", "gmail_select",
    "whatsapp_contact", "busy_mode_disable", "brain_ask",
    "phone_start", "phone_stop", "phone_invite", "phone_timer", "phone_alarm", "phone_transfer",
    "diagnostics_internet",
}
_CONFIRM = {
    "power_plan", "close_app", "delete_item", "whatsapp_message", "whatsapp_call",
    "gmail_compose", "busy_mode_enable", "busy_mode_draft", "approve_app", "gmail_disconnect", "phone_revoke",
}
_TYPED_YES = {"clean_downloads", "file_move", "file_rename", "file_recycle"}
_REFUSE = {"force_close_refused"}

# Sources that can never approve anything.
_NEVER_CONFIRM = {"voice", "workflow", "brain", "phone", "scheduler", "model"}


def policy_for(intent_name: str) -> str:
    if intent_name in _REFUSE:
        return REFUSE
    if intent_name in _TYPED_YES:
        return TYPED_YES
    if intent_name in _CONFIRM:
        return CONFIRM
    if intent_name in _DIRECT:
        return DIRECT
    return REFUSE  # unknown action names fail closed


def confirmation_ok(level: str, source: str, text: str = "") -> bool:
    """May this approval attempt satisfy `level`?

    CONFIRM: a typed message or an explicit UI click.
    TYPED_YES: only the exact typed word "yes".
    Voice, workflow data, model output, phone and scheduler never confirm.
    """
    if source in _NEVER_CONFIRM:
        return False
    if level == CONFIRM:
        return source in ("typed", "ui")
    if level == TYPED_YES:
        return source == "typed" and (text or "").strip().lower() == "yes"
    return False


# ----------------------------------------------------------------- paths
@dataclass(frozen=True)
class PathEnv:
    windir: str = r"C:\Windows"
    program_files: tuple = (r"C:\Program Files", r"C:\Program Files (x86)")
    program_data: str = r"C:\ProgramData"
    users_dir: str = r"C:\Users"
    profile: str = r"C:\Users\Default"

    @staticmethod
    def from_environ() -> "PathEnv":
        e = os.environ
        pf = tuple(p for p in {e.get("ProgramFiles"), e.get("ProgramFiles(x86)"),
                               e.get("ProgramW6432")} if p) or (r"C:\Program Files",)
        profile = e.get("USERPROFILE", r"C:\Users\Default")
        return PathEnv(
            windir=e.get("SystemRoot", r"C:\Windows"), program_files=pf,
            program_data=e.get("ProgramData", r"C:\ProgramData"),
            users_dir=ntpath.dirname(profile.rstrip("\\")) or r"C:\Users", profile=profile)


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    code: str
    reason: str = ""


def _norm(p: str) -> str:
    n = ntpath.normcase(ntpath.normpath(p))
    return n if len(n) <= 3 else n.rstrip("\\")


def _strip_ext(p: str) -> str:
    return p[4:] if p.startswith("\\\\?\\") and len(p) > 6 and p[5] == ":" else p


def _is_unc_or_device(p: str) -> bool:
    q = p.replace("/", "\\")
    return q.startswith("\\\\")


def _is_drive_root(p: str) -> bool:
    drive, rest = ntpath.splitdrive(p)
    return bool(drive) and ntpath.normpath(rest or "\\") == "\\"


def _within(child: str, parent: str) -> bool:
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p.rstrip("\\") + "\\")


def _protected_hit(p: str, env: PathEnv) -> Optional[str]:
    if _is_drive_root(p):
        return "drive root"
    for label, loc in (("Windows", env.windir), ("Program Files", None),
                       ("ProgramData", env.program_data)):
        locs = env.program_files if label == "Program Files" else (loc,)
        for l in locs:
            if _within(p, l):
                return label
    if _norm(p) == _norm(env.profile):
        return "user profile root"
    if _norm(p) == _norm(env.users_dir):
        return "Users folder"
    if _within(p, env.users_dir) and not _within(p, env.profile):
        return "another user's folder"
    return None


def default_is_reparse(p: str) -> bool:
    try:
        st = os.lstat(p)
    except FileNotFoundError:
        return False
    except OSError:
        return True  # fail closed
    return bool(getattr(st, "st_file_attributes", 0) & 0x400) or stat.S_ISLNK(st.st_mode)


def validate_approved_root(root: str, env: PathEnv) -> Verdict:
    if not isinstance(root, str) or not root or "\0" in root:
        return Verdict(False, "invalid", "empty or invalid path")
    drive, rest = ntpath.splitdrive(root)
    if (_is_unc_or_device(root) or not drive or not rest.startswith(("\\", "/")) or
            ":" in rest or ".." in rest.replace("/", "\\").split("\\")):
        return Verdict(False, "invalid", "must be a local absolute path")
    hit = _protected_hit(root, env)
    if hit:
        return Verdict(False, "protected", f"cannot approve {hit}")
    for loc in (env.windir, env.program_data, env.profile, env.users_dir, *env.program_files):
        if _within(loc, root):
            return Verdict(False, "protected", "folder contains a protected location")
    return Verdict(True, "ok")


def check_mutation_path(path: str, approved_roots: Iterable[str], env: PathEnv, *,
                        realpath: Callable[[str], str] = os.path.realpath,
                        is_reparse: Callable[[str], bool] = default_is_reparse) -> Verdict:
    """Decide whether Jarvix may move/rename/recycle `path`. Fails closed."""
    try:
        if not isinstance(path, str) or not path or "\0" in path:
            return Verdict(False, "invalid", "empty or invalid path")
        if _is_unc_or_device(path):
            return Verdict(False, "unc_device", "network and device paths are refused")
        drive, rest = ntpath.splitdrive(path)
        if not drive or not rest.startswith(("\\", "/")):
            return Verdict(False, "relative", "path must be absolute")
        if ":" in rest:
            return Verdict(False, "stream", "alternate data streams are refused")
        orig = ntpath.normpath(path)
        hit = _protected_hit(orig, env)
        if hit:
            return Verdict(False, "protected", f"refused: {hit}")

        resolved = _strip_ext(realpath(orig))
        if _is_unc_or_device(resolved):
            return Verdict(False, "unc_device", "path resolves to a network/device location")
        hit = _protected_hit(resolved, env)
        if hit:
            return Verdict(False, "protected", f"resolves into {hit}")

        roots = [r for r in approved_roots if validate_approved_root(r, env).allowed]
        for root in roots:
            root = ntpath.normpath(root)
            rroot = _strip_ext(realpath(root))
            if is_reparse(root) or _norm(rroot) != _norm(root):
                continue
            if _norm(orig) == _norm(root) or _norm(resolved) == _norm(rroot):
                return Verdict(False, "is_root", "the approved folder itself cannot be changed")
            if _within(orig, root) and _within(resolved, rroot):
                # no reparse point anywhere between the root and the target
                cur = orig
                while _norm(cur) != _norm(root):
                    if is_reparse(cur):
                        return Verdict(False, "reparse", "link or junction in path")
                    parent = ntpath.dirname(cur)
                    if parent == cur:
                        return Verdict(False, "reparse", "could not verify path")
                    cur = parent
                return Verdict(True, "ok", "inside approved folder")
        return Verdict(False, "outside_roots", "not inside an approved folder")
    except Exception as exc:  # fail closed on anything unexpected
        return Verdict(False, "error", f"could not verify path: {type(exc).__name__}")
