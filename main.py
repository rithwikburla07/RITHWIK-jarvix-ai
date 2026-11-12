"""Jarvix Personal AI v1.1.7 - checkpoint 5a.

  python main.py            opens the desktop window (needs PySide6)
  python main.py --console  text-only mode, same engine

Tasks/reminders are saved in %LOCALAPPDATA%\\Jarvix\\jarvix.db. Power and close
actions need a typed 'yes'. Quit the window or type 'quit' in console mode.
"""
import json
from functools import lru_cache
import os
import subprocess
import sys
import threading
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from jarvis import __version__
from jarvis.config.paths import ensure_data_dir
from jarvis.config.settings import Settings
from jarvis.core.engine import Engine
from jarvis.core.parser import Parser, default_catalog
from jarvis.core.safety import PathEnv
from jarvis.i18n.locale import Locale
from jarvis.integrations.brain import Brain
from jarvis.integrations.diagnostics import internet
from jarvis.integrations.gmail import Gmail
from jarvis.integrations.music import Music
from jarvis.integrations.phone import PhoneLink
from jarvis.integrations.web import WebActions
from jarvis.integrations.whatsapp import WhatsApp
from jarvis.logging_setup import configure as configure_logging, record
from jarvis.system.actions import SystemActions
from jarvis.system.apps import AppRegistry, load_apps
from jarvis.system.desktop import DesktopFiles, local_directory
from jarvis.system.power import PowerController
from jarvis.system.search import approved_roots
from jarvis.system.shortcuts import AppApprovals
from jarvis.system.sysinfo import SysInfo
from jarvis.system.volume import VolumeController
from jarvis.tasks.locking import SingleInstanceLock
from jarvis.tasks.repository import Repository, SchemaTooNewError
from jarvis.tasks.scheduler import Alert, Scheduler
from jarvis.tasks.service import TaskService, fmt_due
from jarvis.workflows.loader import WorkflowError, load_workflows
from jarvis.workflows.runner import WorkflowRunner
from jarvis.workflows.service import WorkflowService

HERE = Path(__file__).resolve().parent
_print_lock = threading.Lock()
SYSROOT = os.environ.get("SystemRoot", "C:\\Windows")


def capture(argv, timeout=15):
    """Run a fixed argument list (never a shell). Returns (returncode, stdout)."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, shell=False, check=False)
        return p.returncode, p.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def run(argv) -> int:
    return capture(argv)[0]


@lru_cache(maxsize=1)
def gpu_name() -> str:
    ps = os.path.join(SYSROOT, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    rc, out = capture([ps, "-NoProfile", "-Command", "(Get-CimInstance Win32_VideoController).Name"])
    return out.strip() if rc == 0 and out.strip() else "not available"


def build_system() -> SystemActions:
    try:
        entries = load_apps(HERE / "data" / "apps.json")
    except (OSError, ValueError) as exc:
        print(f"(app list not loaded: {exc})")
        entries = []
    return SystemActions(AppRegistry(entries), VolumeController(), SysInfo(gpu_fn=gpu_name),
                         PowerController(capture, system_root=SYSROOT), run)


def folder_paths() -> dict:
    profile = os.environ.get("USERPROFILE", "")
    return {"desktop": os.path.join(profile, "Desktop"),
            "documents": os.path.join(profile, "Documents"),
            "downloads": os.path.join(profile, "Downloads"),
            "notes": os.path.join(profile, "Documents", "Notes")}


def _is_link_like(path: str) -> bool:
    """Reject symlinks and Windows junctions before opening an approved folder."""
    if os.path.islink(path):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    return bool(isjunction and isjunction(path))


def open_folder(key: str) -> bool:
    """Open only an approved, non-link local folder, including no junction escapes."""
    path = folder_paths().get(key, "")
    if not path or not os.path.isdir(path) or _is_link_like(path):
        return False
    try:
        real = os.path.normcase(os.path.realpath(path))
        roots = [os.path.normcase(os.path.realpath(r)) for r in approved_roots(dict(os.environ))]
        if not any(os.path.commonpath((real, root)) == root for root in roots):
            return False
    except (OSError, ValueError):
        return False
    return reveal_folder(real)


def reveal_folder(path: str) -> bool:
    subprocess.Popen([os.path.join(SYSROOT, "explorer.exe"), path], shell=False,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def open_url(url: str) -> bool:
    return bool(webbrowser.open(url, new=2))


def build_catalog():
    cat = default_catalog()
    sample = HERE / "data" / "contacts.sample.json"
    try:
        cat.contacts = {c["name"]: c.get("aliases", []) for c in json.loads(sample.read_text(encoding="utf-8"))["contacts"]}
        cat.__post_init__()
    except Exception:
        print("(no sample contacts loaded)")
    return cat


def alert_text(alert: Alert) -> str:
    now = datetime.now()
    tag = "MISSED" if alert.missed else "reminder"
    when = f" (was due {fmt_due(alert.item.effective_due, now)})" if alert.missed else ""
    return f"{tag} #{alert.item.id}: {alert.item.title}{when}. Say 'snooze my reminder for 10 minutes'."


@dataclass
class Context:
    repo: Repository
    engine: Engine
    lock: SingleInstanceLock
    workflow_names: list = field(default_factory=list)


def bootstrap() -> Context:
    data = ensure_data_dir()
    configure_logging(data)
    repo = Repository(data / "jarvix.db")
    try:
        engine = Engine(Parser(build_catalog()), TaskService(repo), system=build_system())
        engine.profile = data
        engine.settings = Settings(data / "settings.json")
        engine.parser.locale = Locale(engine.settings.get("language"))
        apps = AppApprovals(engine.system.apps, engine.parser.cat, data / "approved-apps.json")
        files = DesktopFiles(engine.system.apps, reveal_folder, engine.settings)
        engine.app_approvals = apps
        engine.music = Music(engine.settings)
        engine.gmail = Gmail(data / "gmail-accounts.json", open_url)
        engine.whatsapp = WhatsApp(data / "contacts.json", engine.parser.cat, engine.settings, open_url)
        engine.phone = PhoneLink(data, files.roots, engine.whatsapp)
        brain = Brain(engine.settings)
        def open_approved_folder(key):
            if key == "music":
                path = engine.settings.get("music_folder")
                return bool(path and local_directory(path, PathEnv.from_environ()) and reveal_folder(path))
            return open_folder(key)
        web = WebActions(open_url, open_approved_folder)
        engine.handlers.update({
            "file_search": files.find, "reveal_search_hit": files.open_hit, "clean_downloads": files.cleanup,
            "discover_apps": apps.discover, "approve_app": apps.approve, "app_alias": apps.set_alias,
            "brain_ask": brain.handle, "whatsapp_contact": engine.whatsapp.save_contact,
            "diagnostics_internet": internet,
            "phone_start": engine.phone.start, "phone_stop": engine.phone.stop_intent,
            "phone_invite": engine.phone.invite, "phone_revoke": engine.phone.revoke,
            **dict.fromkeys(("phone_open_app", "phone_dial", "phone_ring", "phone_timer", "phone_alarm",
                             "phone_transfer"), engine.phone.action),
            **dict.fromkeys(("whatsapp_message", "whatsapp_call", "whatsapp_unsupported", "busy_mode_enable",
                             "busy_mode_disable", "busy_mode_draft"), engine.whatsapp.handle),
            **dict.fromkeys(("gmail_unread", "gmail_latest", "gmail_drafts", "gmail_read"), engine.gmail.read),
            "gmail_connect": engine.gmail.connect, "gmail_disconnect": engine.gmail.disconnect,
            "gmail_select": engine.gmail.choose, "gmail_compose": engine.gmail.compose,
            **dict.fromkeys(("music_play", "music_select", "music_pause", "music_resume", "music_scan",
                             "music_stop", "music_next", "music_previous"), engine.music.handle),
            **dict.fromkeys(("open_site", "open_url", "open_folder", "web_search", "youtube_search"), web.handle),
        })
        wfs = None
        try:
            wfs = load_workflows(HERE / "data" / "workflows.json")
        except (OSError, ValueError, WorkflowError) as exc:
            print(f"(workflows not loaded: {exc})")
        names = []
        if wfs is not None:
            runner = WorkflowRunner(engine.workflow_step, open_url, open_approved_folder,
                                    study_url=lambda: engine.settings.get("study_url"))
            engine.workflows = WorkflowService(wfs, runner, data / "workflows.json", engine.parser.cat)
            engine.handlers["workflow_save"] = engine.workflows.handle_editor
            names = engine.workflows.names()
        return Context(repo, engine, SingleInstanceLock(data / "jarvix.lock"), names)
    except BaseException:
        repo.close()
        raise


def start_scheduler(ctx: Context, on_alert) -> tuple[Scheduler, bool]:
    """Acquire once; caller is responsible for stopping scheduler and releasing owned lock."""
    sched = Scheduler(ctx.repo, lambda a: on_alert(alert_text(a)), interval=5.0)
    owns_lock = ctx.lock.acquire()
    try:
        if owns_lock:
            sched.tick()
            sched.start()
    except BaseException:
        if owns_lock:
            sched.stop()
            ctx.lock.release()
        raise
    return sched, owns_lock


def console_main(ctx: Context) -> int:
    def console_alert(text: str) -> None:
        with _print_lock:
            print(f"\n*** {text} ***\nyou> ", end="", flush=True)
    sched, owns_lock = start_scheduler(ctx, console_alert)
    if not owns_lock:
        print("Another Jarvix is already using this profile.")
        return 1
    print("Console mode. Type 'help' or 'quit'.\n")
    try:
        while True:
            try:
                text = input("you> ")
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if text.strip().lower() in ("quit", "exit", "q"):
                break
            try:
                reply = ctx.engine.handle(text, source="typed")
                parser = getattr(ctx.engine, "parser", None)
                if parser is not None:
                    reply = parser.locale.response(reply)
                record(reply)
            except Exception as exc:
                print(f"jarvix> internal error: {type(exc).__name__}")
                continue
            with _print_lock:
                print(f"jarvix> {reply.text}")
            if reply.kind == "exit":
                break
    finally:
        # Never release a lease that another process owns.
        while not sched.stop(timeout=5.0):
            print("Waiting for a reminder scheduler callback to finish safely...")
        if owns_lock:
            ctx.lock.release()
    return 0


def gui_main(ctx: Context) -> int:
    from jarvis.ui.app import run_gui

    def start_services(bridge):
        sched, owns_lock = start_scheduler(ctx, bridge.alert.emit)
        if not owns_lock:
            raise RuntimeError("Another Jarvix is already using this profile.")
        def cleanup():
            while not sched.stop(timeout=5.0):
                print("Waiting for the reminder scheduler to finish safely...")
            if owns_lock:
                ctx.lock.release()
        return cleanup

    return run_gui(ctx.engine, ctx.workflow_names, start_services)


def is_admin() -> bool:
    """Windows: reject elevated operation before creating application state."""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        if os.name == 'nt':
            raise RuntimeError('Cannot determine Windows elevation status safely')
        return False


def main(argv) -> int:
    print(f"Jarvix Personal AI v{__version__} - Your PC. Your Commands. Your Intelligence.")
    if is_admin():
        print("Jarvix refuses to run as administrator. Start it normally, without 'Run as administrator'.")
        return 1
    try:
        ctx = bootstrap()
    except SchemaTooNewError as exc:
        print(f"Can't open your data: {exc}. Update Jarvix.")
        return 1
    try:
        if "--console" in argv:
            return console_main(ctx)
        try:
            import PySide6  # noqa: F401
        except ImportError:
            print("The window needs PySide6. Run: .\\.venv\\Scripts\\python.exe -m pip install PySide6")
            print("Starting console mode instead.")
            return console_main(ctx)
        return gui_main(ctx)
    finally:
        # console_main and gui_main have already stopped their workers/schedulers.
        ctx.lock.release()  # idempotent if the branch already released it
        ctx.repo.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
