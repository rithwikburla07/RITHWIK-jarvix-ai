import types
from datetime import datetime

from jarvis.core.intents import Intent
from jarvis.system.actions import SystemActions
from jarvis.system.apps import AppRegistry, load_apps
from jarvis.system.power import PowerController, parse_list
from jarvis.system.sysinfo import SysInfo, SysInfoError
from jarvis.system.volume import VolumeController, VolumeUnavailable, clamp
from pathlib import Path

NOW = datetime(2026, 11, 12, 10, 0)
APPS_FILE = Path(__file__).resolve().parents[1] / "data" / "apps.json"
POWER_TEXT = """Existing Power Schemes (* Active)
Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced) *
Power Scheme GUID: a1841308-3541-4fab-bc81-f71556f20b4a  (Power saver)
Power Scheme GUID: 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c  (High performance)
"""
ENV = {"SystemRoot": "C:\\Windows", "USERPROFILE": "C:\\Users\\Rithwik"}
FILES = {"C:\\Windows\\System32\\notepad.exe", "C:\\Windows\\System32\\taskkill.exe",
         "C:\\Windows\\System32\\powercfg.exe", "C:\\Windows\\explorer.exe"}


class FakeVol:
    def __init__(self, level=40):
        self.level, self.muted = level, False

    def get_percent(self):
        return self.level

    def set_percent(self, p):
        self.level = p

    def get_mute(self):
        return self.muted

    def set_mute(self, m):
        self.muted = m


class FakePs:
    def __init__(self, battery=None):
        self._battery = battery

    def sensors_battery(self):
        return self._battery

    def cpu_percent(self, interval=None):
        return 12.0

    def cpu_count(self, logical=True):
        return 8

    def virtual_memory(self):
        return types.SimpleNamespace(percent=50.0, used=4 * 1024**3, total=8 * 1024**3)

    def disk_partitions(self, all=False):
        return [types.SimpleNamespace(device="C:", mountpoint="C:\\")]

    def disk_usage(self, p):
        return types.SimpleNamespace(free=100 * 1024**3, total=250 * 1024**3)


def make(files=FILES, vol=None, battery=None, power_text=POWER_TEXT, power_rc=0, psutil=None):
    calls = []
    popens = []

    def run(argv):
        calls.append(argv)
        return 0

    def capture(argv):
        if argv[-1] == "/list":
            return power_rc, power_text
        return 0, ""

    apps = AppRegistry(load_apps(APPS_FILE), env=ENV, isfile=lambda p: p in files,
                       popen=lambda argv, **kw: popens.append((argv, kw)), is_windows=True)
    acts = SystemActions(apps, VolumeController(vol or FakeVol()),
                         SysInfo(psutil_mod=psutil or FakePs(battery), gpu_fn=lambda: "Intel Arc"),
                         PowerController(capture), run)
    return acts, calls, popens


def I(name, **slots):
    return Intent(name, slots, raw="")


def test_clamp_and_types():
    assert clamp(150) == 100 and clamp(-5) == 0 and clamp(49.6) == 50
    for bad in ("50", True, None):
        try:
            clamp(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_set_volume_clamps_and_reports():
    v = FakeVol()
    acts, *_ = make(vol=v)
    reply, action = acts.handle(I("set_volume", level=50), NOW)
    assert v.level == 50 and action is None and "50%" in reply.text


def test_volume_step_and_mute_direct():
    v = FakeVol(level=95)
    acts, *_ = make(vol=v)
    acts.handle(I("volume_up", step=10), NOW)
    assert v.level == 100
    acts.handle(I("volume_down"), NOW)
    assert v.level == 90
    acts.handle(I("mute"), NOW)
    assert v.muted is True
    acts.handle(I("unmute"), NOW)
    assert v.muted is False


def test_volume_unavailable_is_a_clear_message_not_a_crash():
    def boom():
        raise RuntimeError("no audio device")
    acts, *_ = make()
    acts.volume = VolumeController(factory=boom)
    reply, _ = acts.handle(I("set_volume", level=20), NOW)
    assert reply.kind == "unavailable" and "isn't available" in reply.text


def test_open_app_launches_directly_without_confirmation():
    acts, calls, popens = make()
    reply, action = acts.handle(I("open_app", app="Notepad"), NOW)
    assert reply.kind == "done" and action is None
    assert popens[-1][0] == ["C:\\Windows\\System32\\notepad.exe"] and popens[-1][1]["shell"] is False


def test_open_missing_app_is_reported():
    acts, _, popens = make()
    reply, _ = acts.handle(I("open_app", app="Edge"), NOW)
    assert reply.kind == "unavailable" and popens == []


def test_close_requires_confirmation_and_sends_graceful_request():
    acts, calls, _ = make()
    reply, action = acts.handle(I("close_app", app="Notepad"), NOW)
    assert reply.kind == "confirm" and action is not None and calls == []   # nothing happened yet
    done = action(NOW)
    assert done.kind == "done" and calls[-1] == ["C:\\Windows\\System32\\taskkill.exe", "/IM", "notepad.exe"]


def test_close_reports_not_running_and_failure_codes():
    acts, calls, _ = make()
    _, action = acts.handle(I("close_app", app="Notepad"), NOW)
    acts.run = lambda argv: 128
    assert action(NOW).kind == "info"
    acts.run = lambda argv: 1
    assert action(NOW).kind == "error"


def test_protected_apps_refused_before_any_prompt():
    acts, calls, _ = make()
    reply, action = acts.handle(I("close_app", app="Calculator"), NOW)
    assert reply.kind == "refused" and action is None and calls == []


def test_sysinfo_battery_none_on_desktop_and_present_on_laptop():
    acts, *_ = make(battery=None)
    assert "No battery" in acts.handle(I("sysinfo", kind="battery"), NOW)[0].text
    bat = types.SimpleNamespace(percent=73.4, power_plugged=False)
    acts, *_ = make(battery=bat)
    assert acts.handle(I("sysinfo", kind="battery"), NOW)[0].text == "Battery 73%, on battery."


def test_sysinfo_status_and_gpu():
    acts, *_ = make()
    text = acts.handle(I("sysinfo", kind="status"), NOW)[0].text
    assert "CPU 12%" in text and "Memory 50%" in text and "C: 100.0 GB free" in text
    assert acts.handle(I("sysinfo", kind="gpu"), NOW)[0].text == "GPU: Intel Arc"


def test_sysinfo_without_psutil_gives_install_hint(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "psutil", None)      # makes `import psutil` fail
    try:
        SysInfo().report("cpu")
    except SysInfoError as exc:
        assert "pip install -r requirements.txt" in str(exc)
        return
    raise AssertionError("expected SysInfoError")


def test_parse_power_plans_and_active_flag():
    plans = parse_list(POWER_TEXT)
    assert [p.name for p in plans] == ["Balanced", "Power saver", "High performance"]
    assert [p.active for p in plans] == [True, False, False]


def test_power_change_needs_confirmation_then_runs_setactive():
    acts, calls, _ = make()
    reply, action = acts.handle(I("power_plan", plan="saver"), NOW)
    assert reply.kind == "confirm" and "Balanced" in reply.text and "Power saver" in reply.text
    assert calls == []                                       # no change before confirmation
    done = action(NOW)
    assert done.kind == "done"
    assert calls[-1] == ["C:\\Windows\\System32\\powercfg.exe", "/setactive", "a1841308-3541-4fab-bc81-f71556f20b4a"]


def test_missing_power_plan_opens_battery_settings_instead():
    acts, calls, _ = make(power_text="Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced) *\n")
    reply, action = acts.handle(I("power_plan", plan="saver"), NOW)
    assert action is None and reply.kind == "info"
    assert calls[-1] == ["C:\\Windows\\explorer.exe", "ms-settings:batterysaver"]


def test_power_list_failure_is_reported():
    acts, *_ = make(power_rc=1)
    reply, action = acts.handle(I("power_plan", plan="balanced"), NOW)
    assert reply.kind == "unavailable" and action is None


def test_unknown_plan_name_refused():
    acts, *_ = make()
    assert acts.handle(I("power_plan", plan="turbo-nonsense"), NOW)[0].kind == "error"
