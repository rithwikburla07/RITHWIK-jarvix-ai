import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from jarvis.core import fileplan as fp
from jarvis.core.safety import (CONFIRM, DIRECT, REFUSE, TYPED_YES, PathEnv, check_mutation_path,
                                confirmation_ok, policy_for, validate_approved_root)
from tests.helpers import NOW

ENV = PathEnv(profile=r"C:\Users\Rithwik", users_dir=r"C:\Users")
DL = r"C:\Users\Rithwik\Downloads"
ROOTS = [DL]
ident = lambda p: p
no_link = lambda p: False


def chk(p, roots=ROOTS, realpath=ident, is_reparse=no_link):
    return check_mutation_path(p, roots, ENV, realpath=realpath, is_reparse=is_reparse)


def test_refuses_system_and_escaped_paths():
    for p in ["C:\\", "C:", "c:/", r"C:\Windows", r"C:\Windows\System32\cmd.exe", r"C:\Program Files\App\a.exe",
              r"C:\Program Files (x86)\App", r"C:\ProgramData\x", r"C:\Users\Rithwik", r"C:\Users",
              r"C:\Users\Other\Downloads\a.exe", r"\\server\share\a.exe", r"\\.\PhysicalDrive0",
              r"\\?\C:\Windows\x", r"C:\Users\Rithwik\Documents\a.txt", r"D:\stuff\a.exe",
              r"C:\Users\Rithwik\Downloads\..\Documents\a.txt", "relative\\a.exe", "", r"C:\Users\Rithwik\Downloads\a.exe:evil"]:
        assert not chk(p).allowed, p


def test_allows_inside_approved_root_case_insensitive():
    assert chk(r"c:\users\rithwik\DOWNLOADS\Old.EXE").allowed
    assert chk(r"C:\Users\Rithwik\Downloads\sub\a.msi").allowed


def test_approved_root_itself_is_not_mutable():
    assert chk(DL).code == "is_root"


def test_junction_escape_resolved_and_refused():
    real = lambda p: r"C:\Windows\System32" if p.lower().endswith("evil") else p
    assert chk(DL + r"\evil\cmd.exe", realpath=lambda p: r"C:\Windows\System32\cmd.exe").allowed is False
    assert chk(DL + r"\evil", realpath=real).allowed is False


def test_reparse_component_refused():
    link = lambda p: p.lower().endswith("linkdir")
    assert chk(DL + r"\linkdir\a.exe", is_reparse=link).code == "reparse"


def test_fail_closed_on_errors():
    def boom(p):
        raise OSError("denied")
    assert not chk(DL + r"\a.exe", realpath=boom).allowed


def test_cannot_approve_dangerous_roots():
    for r in ["C:\\", r"C:\Windows", r"C:\Users", r"C:\Users\Rithwik", r"C:\Program Files", r"\\srv\x", "rel"]:
        assert not validate_approved_root(r, ENV).allowed, r
    assert validate_approved_root(r"C:\Users\Rithwik\Music", ENV).allowed
    assert validate_approved_root(r"D:\Media", ENV).allowed


def test_policy_levels():
    assert policy_for("open_app") == DIRECT and policy_for("set_volume") == DIRECT
    assert policy_for("power_plan") == CONFIRM and policy_for("close_app") == CONFIRM
    assert policy_for("delete_item") == CONFIRM and policy_for("whatsapp_message") == CONFIRM
    assert policy_for("gmail_compose") == CONFIRM and policy_for("busy_mode_enable") == CONFIRM
    assert policy_for("clean_downloads") == TYPED_YES
    assert policy_for("force_close_refused") == REFUSE
    assert policy_for("totally_new_action") == REFUSE   # fail closed


def test_only_typed_or_ui_can_confirm():
    assert confirmation_ok(CONFIRM, "typed") and confirmation_ok(CONFIRM, "ui")
    for src in ("voice", "workflow", "brain", "phone", "scheduler", "model"):
        assert not confirmation_ok(CONFIRM, src), src
        assert not confirmation_ok(TYPED_YES, src, "yes"), src
    assert confirmation_ok(TYPED_YES, "typed", " Yes ")
    for bad in ("y", "yep", "yes please", "", "no", "ok"):
        assert not confirmation_ok(TYPED_YES, "typed", bad), bad
    assert not confirmation_ok(TYPED_YES, "ui", "yes")


# ---------------------------------------------------------------- file plans
class Fake:
    """In-memory filesystem: path -> (size, mtime_ns)."""
    def __init__(self, files):
        self.files = dict(files)
        self.trashed, self.moved = [], []

    def stat(self, p):
        if p not in self.files:
            raise FileNotFoundError(p)
        return self.files[p]

    def exists(self, p):
        return p in self.files

    def trash(self, p):
        self.trashed.append(p)
        del self.files[p]

    def move(self, s, d):
        self.moved.append((s, d))
        self.files[d] = self.files.pop(s)


def kw(f):
    return dict(realpath=ident, is_reparse=no_link, stat_fn=f.stat, exists_fn=f.exists)


def mkplan(f, entries, dry_run=False, op="recycle", now=NOW):
    return fp.build_plan(op, entries, ROOTS, ENV, now=now, dry_run=dry_run, **kw(f))


A, B = DL + r"\a.exe", DL + r"\b.msi"


def test_dry_run_changes_nothing_even_with_yes():
    f = Fake({A: (10, 1), B: (20, 2)})
    plan = mkplan(f, [A, B], dry_run=True)
    r = fp.execute_plan(plan, "yes", "typed", ROOTS, ENV, now=NOW, trasher=f.trash, **kw(f))
    assert not r.applied and r.reason == "dry_run" and f.trashed == [] and set(f.files) == {A, B}


def test_yes_is_required_and_voice_cannot_confirm():
    f = Fake({A: (10, 1)})
    plan = mkplan(f, [A])
    for text, src in [("", "typed"), ("no", "typed"), ("yes please", "typed"), ("yes", "voice"), ("yes", "workflow"), ("yes", "brain")]:
        r = fp.execute_plan(plan, text, src, ROOTS, ENV, now=NOW, trasher=f.trash, **kw(f))
        assert r.reason == "confirmation_required" and f.trashed == [], (text, src)


def test_typed_yes_recycles_per_item_and_reports():
    f = Fake({A: (10, 1), B: (20, 2)})
    plan = mkplan(f, [A, B])
    r = fp.execute_plan(plan, "yes", "typed", ROOTS, ENV, now=NOW, trasher=f.trash, **kw(f))
    assert r.applied and [i.ok for i in r.items] == [True, True] and f.trashed == [A, B]


def test_stale_plan_by_time_and_by_change():
    f = Fake({A: (10, 1)})
    plan = mkplan(f, [A])
    late = NOW + timedelta(seconds=fp.PLAN_TTL_SECONDS + 1)
    assert fp.execute_plan(plan, "yes", "typed", ROOTS, ENV, now=late, trasher=f.trash, **kw(f)).reason == "stale_plan"
    f.files[A] = (11, 1)   # file changed after preview
    assert fp.execute_plan(plan, "yes", "typed", ROOTS, ENV, now=NOW, trasher=f.trash, **kw(f)).reason == "plan_changed"
    del f.files[A]          # file vanished
    assert fp.execute_plan(plan, "yes", "typed", ROOTS, ENV, now=NOW, trasher=f.trash, **kw(f)).reason == "plan_changed"
    assert f.trashed == []


def test_no_permanent_fallback_when_no_trasher():
    f = Fake({A: (10, 1)})
    plan = mkplan(f, [A])
    orig = fp.default_trasher
    fp.default_trasher = lambda: None
    try:
        r = fp.execute_plan(plan, "yes", "typed", ROOTS, ENV, now=NOW, trasher=None, **kw(f))
    finally:
        fp.default_trasher = orig
    assert r.reason == "recycle_unavailable" and A in f.files


def test_trasher_failure_never_falls_back_to_deletion():
    f = Fake({A: (10, 1)})
    plan = mkplan(f, [A])
    def bad(p):
        raise OSError("recycle bin unavailable")
    r = fp.execute_plan(plan, "yes", "typed", ROOTS, ENV, now=NOW, trasher=bad, **kw(f))
    assert not r.items[0].ok and A in f.files


def test_item_not_actually_removed_is_reported_failed():
    f = Fake({A: (10, 1)})
    plan = mkplan(f, [A])
    r = fp.execute_plan(plan, "yes", "typed", ROOTS, ENV, now=NOW, trasher=lambda p: None, **kw(f))
    assert not r.items[0].ok


def test_refused_items_are_listed_and_excluded():
    f = Fake({A: (10, 1), r"C:\Windows\x.exe": (1, 1)})
    plan = mkplan(f, [A, r"C:\Windows\x.exe", r"C:\Users\Rithwik\Documents\n.txt"])
    assert [i.src for i in plan.items] == [A] and len(plan.refused) == 2


def test_move_rejects_overwrite_and_outside_destination():
    f = Fake({A: (10, 1), DL + r"\c.exe": (5, 1)})
    plan = mkplan(f, [(A, DL + r"\c.exe"), (A, r"C:\Windows\a.exe")], op="move")
    assert plan.items == () and len(plan.refused) == 2
    ok = mkplan(f, [(A, DL + r"\sub\a.exe")], op="move")
    r = fp.execute_plan(ok, "yes", "typed", ROOTS, ENV, now=NOW, mover=f.move, **kw(f))
    assert r.applied and f.moved == [(A, DL + r"\sub\a.exe")]


def test_preview_mentions_recycle_bin_and_dry_run():
    f = Fake({A: (10, 1)})
    assert "DRY RUN" in fp.preview_text(mkplan(f, [A], dry_run=True))
    assert "Recycle Bin" in fp.preview_text(mkplan(f, [A]))


def test_old_installers_selection_on_real_temp_dir():
    with tempfile.TemporaryDirectory() as d:
        now = datetime(2026, 11, 12, 10, 0)
        def mk(name, age_days, data=b"x"):
            p = os.path.join(d, name)
            Path(p).write_bytes(data)
            t = now.timestamp() - age_days * 86400
            os.utime(p, (t, t))
        mk("old.exe", 120); mk("older.MSI", 400); mk("recent.exe", 10); mk("old.txt", 500); mk("edge.exe", 89)
        os.mkdir(os.path.join(d, "sub.exe"))              # a directory named like an installer
        os.mkdir(os.path.join(d, "nested"))
        Path(os.path.join(d, "nested", "deep.exe")).write_bytes(b"x")
        os.utime(os.path.join(d, "nested", "deep.exe"), (1, 1))   # old but NOT directly in Downloads
        got = [os.path.basename(p) for p in fp.old_installers(d, now)]
        assert got == ["old.exe", "older.MSI"]


def test_no_permanent_deletion_calls_in_source():
    root = Path(__file__).resolve().parents[1] / "jarvis"
    pat = re.compile(r"\bos\.(remove|unlink|rmdir)\(|shutil\.rmtree\(|\.unlink\(|\.rmdir\(")
    offenders = []
    for py in root.rglob("*.py"):
        if "maintenance" in py.parts:
            continue   # the uninstaller is the only permitted permanent-deletion path
        for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if pat.search(line):
                offenders.append(f"{py.name}:{n}")
    assert offenders == []


def test_unknown_or_empty_source_never_confirms():
    for src in ("webhook", "", "TYPED ", "remote", None):
        assert not confirmation_ok(CONFIRM, src), src
        assert not confirmation_ok(TYPED_YES, src, "yes"), src
    assert not confirmation_ok("direct", "typed", "yes")      # only CONFIRM / TYPED_YES levels exist
    assert not confirmation_ok("bogus", "typed", "yes")
