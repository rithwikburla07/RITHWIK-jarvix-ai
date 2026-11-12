"""Checkpoint 5a regression tests; all operate on temp or fake resources."""
import os
import platform
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import main as main_module
from jarvis.core.engine import Engine
from jarvis.system.sysinfo import SysInfo
from jarvis.tasks.locking import SingleInstanceLock
from jarvis.tasks.repository import Repository


def test_admin_refuses_before_bootstrap(monkeypatch, capsys):
    monkeypatch.setattr(main_module, "is_admin", lambda: True)
    def forbidden():
        raise AssertionError("bootstrap must not run while elevated")
    monkeypatch.setattr(main_module, "bootstrap", forbidden)
    assert main_module.main([]) == 1
    assert "refuses to run as administrator" in capsys.readouterr().out


def test_console_scheduler_acquires_once_and_releases(tmp_path, monkeypatch):
    repo = Repository(tmp_path / "data.db")
    lock = SingleInstanceLock(tmp_path / "exclusive.lock")
    calls = []
    original = lock.acquire
    def tracked():
        calls.append("acquire")
        return original()
    monkeypatch.setattr(lock, "acquire", tracked)
    engine = SimpleNamespace(handle=lambda *_a, **_kw: SimpleNamespace(text="Bye", kind="exit"))
    ctx = main_module.Context(repo, engine, lock)
    monkeypatch.setattr("builtins.input", lambda _prompt: "quit")
    try:
        assert main_module.console_main(ctx) == 0
        assert calls == ["acquire"]
        second = SingleInstanceLock(tmp_path / "exclusive.lock")
        try:
            assert second.acquire()
        finally:
            second.release()
    finally:
        lock.release()
        repo.close()


def test_repo_close_is_idempotent():
    repo = Repository(":memory:")
    repo.close()
    repo.close()
    assert repo._db is None


def test_repo_concurrent_adds(tmp_path):
    repo = Repository(tmp_path / "concurrency.db")
    now = datetime(2026, 11, 12, 12, 0, 0)
    def add_range(prefix):
        for i in range(20):
            repo.add("reminder", f"{prefix}-{i}", now + timedelta(hours=1), now)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(add_range, ["left", "right"]))
        assert len(repo.items_between(now, now + timedelta(days=1))) == 40
    finally:
        repo.close()


def test_snapshot_numeric_values_and_cpu_priming(monkeypatch):
    calls = []
    fake = SimpleNamespace(
        cpu_percent=lambda interval=None: (calls.append(interval) or 18.5),
        virtual_memory=lambda: SimpleNamespace(percent=42.0),
        sensors_battery=lambda: SimpleNamespace(percent=79.6, power_plugged=True),
        disk_usage=lambda _drive: SimpleNamespace(percent=33.0, free=12 * 1024 ** 3),
    )
    monkeypatch.setenv("SystemDrive", "C:")
    info = SysInfo(psutil_mod=fake)
    first = info.snapshot()
    assert first == {"cpu": 18.5, "ram": 42.0, "battery": {"percent": 80, "plugged": True},
                     "storage": {"percent": 33.0, "free_gb": 12.0}}
    assert len(calls) == 2  # priming + reading
    info.snapshot()
    assert len(calls) == 3  # only reading after priming


def test_snapshot_battery_absent(monkeypatch):
    fake = SimpleNamespace(
        cpu_percent=lambda interval=None: 5.0,
        virtual_memory=lambda: SimpleNamespace(percent=10.0),
        sensors_battery=lambda: None,
        disk_usage=lambda _drive: SimpleNamespace(percent=20.0, free=1024 ** 3),
    )
    assert SysInfo(psutil_mod=fake).snapshot()["battery"] is None


def test_workflow_step_alias():
    assert Engine.workflow_step is Engine._workflow_step


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions only")
def test_open_folder_rejects_disposable_junction(monkeypatch, tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "junction"
    command = ["cmd", "/c", "mklink", "/J", str(link), str(target)]
    res = subprocess.run(command, text=True, capture_output=True, check=False)
    if res.returncode != 0:
        pytest.skip(f"junction fixture unavailable: {res.stderr.strip()}")
    monkeypatch.setattr(main_module, "folder_paths", lambda: {"notes": str(link)})
    monkeypatch.setattr(main_module, "approved_roots", lambda _env: [str(tmp_path)])
    monkeypatch.setattr(main_module.subprocess, "Popen", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("Explorer must not launch")))
    assert main_module._is_link_like(str(link))
    assert not main_module.open_folder("notes")


def test_worker_stop_reports_timeout_without_running_qt(monkeypatch):
    pytest.importorskip("PySide6")
    from jarvis.ui.app import Bridge, CommandWorker
    worker = CommandWorker(None, Bridge())
    monkeypatch.setattr(CommandWorker, "wait", lambda self, _ms: False)
    assert worker.stop() is False


def test_gui_service_cleanup_stops_scheduler_before_repository(monkeypatch):
    pytest.importorskip("PySide6")
    from jarvis.ui import app as ui
    order = []
    class FakeRepo:
        def close(self):
            order.append("repo.close")
    class FakeLock:
        def acquire(self):
            order.append("lock.acquire")
            return True
        def release(self):
            order.append("lock.release")
    class FakeScheduler:
        def __init__(self, *_args, **_kwargs):
            pass
        def tick(self):
            order.append("scheduler.tick")
        def start(self):
            order.append("scheduler.start")
        def stop(self, timeout=5.0):
            order.append("scheduler.stop")
            return True
    monkeypatch.setattr(main_module, "Scheduler", FakeScheduler)
    ctx = main_module.Context(FakeRepo(), object(), FakeLock(), [])
    def fake_run_gui(_engine, _names, start_services):
        bridge = SimpleNamespace(alert=SimpleNamespace(emit=lambda *_a: None))
        cleanup = start_services(bridge)
        cleanup()
        ctx.repo.close()   # main() owns final repository close
        return 0
    monkeypatch.setattr(ui, "run_gui", fake_run_gui)
    assert main_module.gui_main(ctx) == 0
    assert order.index("scheduler.stop") < order.index("repo.close")
    assert order.index("lock.release") < order.index("repo.close")
