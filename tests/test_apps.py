import json
from pathlib import Path

from jarvis.system.apps import AppError, AppRegistry, load_apps

APPS_FILE = Path(__file__).resolve().parents[1] / "data" / "apps.json"
ENV = {"SystemRoot": "C:\\Windows", "ProgramFiles": "C:\\Program Files",
       "ProgramFiles(x86)": "C:\\Program Files (x86)",
       "LOCALAPPDATA": "C:\\Users\\Rithwik\\AppData\\Local", "USERPROFILE": "C:\\Users\\Rithwik"}
FILES = {"C:\\Windows\\System32\\notepad.exe", "C:\\Windows\\System32\\cmd.exe",
         "C:\\Windows\\System32\\calc.exe", "C:\\Windows\\explorer.exe",
         "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"}


class FakePopen:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))


def reg(popen=None, lookup=None, is_windows=True, extra_files=()):
    files = FILES | set(extra_files)
    return AppRegistry(load_apps(APPS_FILE), env=ENV, isfile=lambda p: p in files,
                       app_path_lookup=lookup or (lambda exe: None),
                       popen=popen or FakePopen(), is_windows=is_windows)


def test_shipped_app_list_has_the_fourteen_required_apps():
    names = {a.name for a in load_apps(APPS_FILE)}
    assert names == {"Notepad", "Calculator", "Chrome", "Edge", "Explorer", "Paint", "Command Prompt",
                     "PowerShell", "Settings", "Task Manager", "VS Code", "Word", "Excel", "PowerPoint"}


def test_rejects_unsafe_entries(tmp_path):
    cases = [
        [{"name": "Evil", "exe": "x\\..\\evil.exe"}],                  # path in exe name
        [{"name": "Evil", "exe": "calc.exe", "args": ["a&b"]}],       # shell metacharacter
        [{"name": "Evil", "exe": "calc"}],                            # not an .exe
        [{"name": "A", "exe": "a.exe"}, {"name": "a", "exe": "b.exe"}],  # duplicate name
        ["not an object"],
    ]
    for i, data in enumerate(cases):
        p = tmp_path / f"apps{i}.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        try:
            load_apps(p)
        except ValueError:
            continue
        raise AssertionError(f"accepted unsafe entry: {data}")


def test_alias_lookup_is_case_insensitive():
    r = reg()
    assert r.find("CMD").name == "Command Prompt"
    assert r.find("vscode").name == "VS Code"
    assert r.find("rm -rf") is None and r.find("") is None


def test_resolve_expands_fixed_env_vars_and_checks_basename():
    r = reg()
    assert r.resolve_exe(r.find("Notepad")) == "C:\\Windows\\System32\\notepad.exe"
    assert r.resolve_exe(r.find("Chrome")) == "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
    assert r.resolve_exe(r.find("Edge")) is None          # not installed in this fake PC


def test_registry_lookup_only_used_when_it_matches_the_exe():
    r = reg(lookup=lambda exe: "D:\\Apps\\WINWORD.EXE", extra_files=["D:\\Apps\\WINWORD.EXE"])
    assert r.resolve_exe(r.find("Word")) == "D:\\Apps\\WINWORD.EXE"
    r2 = reg(lookup=lambda exe: "D:\\Apps\\other.exe", extra_files=["D:\\Apps\\other.exe"])
    assert r2.resolve_exe(r2.find("Word")) is None


def test_launch_uses_argument_list_and_never_a_shell():
    p = FakePopen()
    r = reg(popen=p)
    r.launch(r.find("Notepad"))
    argv, kw = p.calls[-1]
    assert argv == ["C:\\Windows\\System32\\notepad.exe"]
    assert kw["shell"] is False
    assert "creationflags" not in kw


def test_empty_console_apps_get_new_console_flag_on_windows_only():
    ps = "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
    FILES.add(ps)
    try:
        p = FakePopen()
        r = reg(popen=p)
        r.launch(r.find("PowerShell"))
        assert p.calls[-1][1]["creationflags"] == 0x10
        assert p.calls[-1][0] == [ps, "-NoLogo", "-NoProfile"]
        r2 = reg(popen=p, is_windows=False)
        r2.launch(r2.find("PowerShell"))
        assert "creationflags" not in p.calls[-1][1]
    finally:
        FILES.discard(ps)


def test_settings_opens_ms_settings_uri_as_argument():
    p = FakePopen()
    r = reg(popen=p)
    r.launch(r.find("Settings"))
    assert p.calls[-1][0] == ["C:\\Windows\\explorer.exe", "ms-settings:"]


def test_missing_app_raises_app_error():
    r = reg()
    try:
        r.launch(r.find("Edge"))
    except AppError:
        return
    raise AssertionError("expected AppError")


def test_close_is_graceful_and_refused_for_protected_apps():
    r = reg()
    argv = r.close_argv(r.find("Notepad"))
    assert argv == ["C:\\Windows\\System32\\taskkill.exe", "/IM", "notepad.exe"]
    assert "/F" not in argv
    for name in ("Calculator", "Explorer", "Command Prompt", "PowerShell", "Settings", "Paint"):
        try:
            r.close_argv(r.find(name))
        except AppError:
            continue
        raise AssertionError(f"{name} must not be closable")
