r"""REAL Windows tests. Run only on Windows, in PowerShell with:
    $env:JARVIX_WIN_INTEGRATION = "1"
    .\.venv\Scripts\python.exe -m pytest -q tests\integration
They use temp files created for the test, never your real folders.
Test items stay in the Recycle Bin; empty them by deleting "jarvix-recycle-test-*" there.
"""
import os
import subprocess
import tempfile
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("JARVIX_WIN_INTEGRATION") != "1",
    reason="Windows integration test (set JARVIX_WIN_INTEGRATION=1 on Windows)")


def _in_recycle_bin(original_path: str) -> bool:
    """Ask the Shell whether an item from this original path is in the Recycle Bin.

    Windows may hide known extensions, so match on the name without extension
    OR on the original full path.
    """
    ps = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32",
                      "WindowsPowerShell", "v1.0", "powershell.exe")
    script = ("$p=$env:JARVIX_TEST_PATH; $stem=[IO.Path]::GetFileNameWithoutExtension($p); "
              "$s=(New-Object -ComObject Shell.Application).NameSpace(10); "
              "[bool]($s.Items() | Where-Object { $_.Name -like ($stem + '*') -or $_.Path -eq $p })")
    env = dict(os.environ, JARVIX_TEST_PATH=original_path)
    out = subprocess.run([ps, "-NoProfile", "-Command", script], capture_output=True, text=True,
                         timeout=60, env=env, shell=False).stdout
    return out.strip().lower() == "true"


def test_send2trash_moves_test_file_into_recycle_bin():
    from send2trash import send2trash
    name = f"jarvix-recycle-test-{uuid.uuid4().hex[:8]}.txt"
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("disposable test file")
        send2trash(path)
        assert not os.path.exists(path)
        assert _in_recycle_bin(path), "file was removed but is not in the Recycle Bin"


def test_confirmed_plan_recycles_only_the_approved_temp_item():
    from datetime import datetime
    from send2trash import send2trash
    from jarvis.core import fileplan as fp
    from jarvis.core.safety import PathEnv

    env = PathEnv.from_environ()
    with tempfile.TemporaryDirectory(dir=os.environ.get("TEMP")) as root:
        target = os.path.join(root, f"jarvix-plan-{uuid.uuid4().hex[:8]}.exe")
        keep = os.path.join(root, "keep-me.txt")
        for p in (target, keep):
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("x")
        now = datetime.now()
        plan = fp.build_plan("recycle", [target], [root], env, now=now, dry_run=False)
        assert len(plan.items) == 1, plan.refused
        res = fp.execute_plan(plan, "yes", "typed", [root], env, now=now, trasher=send2trash)
        assert res.applied and res.items[0].ok
        assert not os.path.exists(target) and os.path.exists(keep)
        assert _in_recycle_bin(target), "planned removal did not reach the Recycle Bin"
