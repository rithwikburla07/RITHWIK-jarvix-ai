"""Windows actions behind the engine: apps, volume, stats, power plans.

Each handler returns (Reply, action). `action` is None for direct actions. For a
state change it is a callable(now) -> Reply that the engine runs only after a
typed or UI confirmation. Nothing here builds a command line from raw text.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from ..core.intents import Intent, Reply
from .apps import AppError, AppRegistry
from .power import PowerController, PowerError
from .sysinfo import SysInfo, SysInfoError
from .volume import VolumeController, VolumeUnavailable

Action = Optional[Callable[[datetime], Reply]]
POWER_NAMES = {"saver": "power saver", "balanced": "balanced",
               "performance": "high performance", "high_performance": "high performance"}


class SystemActions:
    def __init__(self, apps: AppRegistry, volume: VolumeController, sysinfo: SysInfo,
                 power: PowerController, run: Callable[[list], int]):
        self.apps, self.volume, self.sysinfo, self.power, self.run = apps, volume, sysinfo, power, run

    def handle(self, intent: Intent, now: datetime):
        table = {
            "open_app": self._open_app, "close_app": self._close_app,
            "set_volume": self._set_volume, "volume_up": self._volume_step,
            "volume_down": self._volume_step, "mute": self._mute, "unmute": self._mute,
            "sysinfo": self._sysinfo, "power_plan": self._power, "power_list": self._power_list,
        }
        fn = table.get(intent.name)
        if fn is None:
            return Reply("unavailable", "That system action isn't available yet.", "system.none", intent=intent), None
        try:
            return fn(intent, now)
        except (VolumeUnavailable, SysInfoError, PowerError) as exc:
            return Reply("unavailable", str(exc), "system.unavailable", intent=intent), None
        except ValueError as exc:
            return Reply("error", f"I couldn't do that: {exc}.", "system.error", intent=intent), None

    # ------------------------------------------------------------ apps
    def _open_app(self, i: Intent, now):
        e = self.apps.find(i.get("app"))
        if e is None:
            return Reply("error", f"{i.get('app') or 'That app'} isn't in your app list.", "app.unknown", intent=i), None
        try:
            self.apps.launch(e)
        except AppError as exc:
            return Reply("unavailable", f"{e.name} isn't installed here, so I can't open it. ({exc})", "app.missing", intent=i), None
        except OSError as exc:
            return Reply("error", f"{e.name} didn't start ({type(exc).__name__}).", "app.failed", intent=i), None
        return Reply("done", f"Opening {e.name}.", "app.opened", intent=i), None

    def _close_app(self, i: Intent, now):
        e = self.apps.find(i.get("app"))
        if e is None:
            return Reply("error", f"{i.get('app') or 'That app'} isn't in your app list.", "app.unknown", intent=i), None
        if not e.closable:
            return Reply("refused", f"I won't close {e.name} automatically. Please close it from its window, so its save prompts stay in your hands.",
                         "app.not_closable", intent=i), None

        def act(now2: datetime) -> Reply:
            try:
                argv = self.apps.close_argv(e)
            except AppError as exc:
                return Reply("refused", str(exc), "app.not_closable")
            rc = self.run(argv)
            if rc == 0:
                return Reply("done", f"Asked {e.name} to close. If it has unsaved work, it will ask you first.", "app.close_sent", intent=i)
            if rc == 128:
                return Reply("info", f"{e.name} isn't running.", "app.not_running", intent=i)
            return Reply("error", f"Windows didn't close {e.name} (code {rc}).", "app.close_failed", intent=i)

        text = (f"Ask {e.name} to close? This is a normal close request, not a force-kill. "
                f"Every open {e.name} window will be asked. Type 'yes' to confirm.")
        return Reply("confirm", text, "app.close_confirm", intent=i), act

    # ------------------------------------------------------------ volume
    def _set_volume(self, i: Intent, now):
        lvl = self.volume.set(i.get("level"))
        return Reply("done", f"Volume set to {lvl}%.", "volume.set", {"level": lvl}, i), None

    def _volume_step(self, i: Intent, now):
        step = i.get("step") or 10
        if not isinstance(step, int) or not 1 <= step <= 100:
            step = 10
        sign = -1 if i.name == "volume_down" else 1
        lvl = self.volume.step(sign * step)
        return Reply("done", f"Volume {'down' if sign < 0 else 'up'} to {lvl}%.", "volume.step", {"level": lvl}, i), None

    def _mute(self, i: Intent, now):
        muted = i.name == "mute"
        self.volume.set_mute(muted)
        return Reply("done", "Muted." if muted else "Unmuted.", "volume.mute" if muted else "volume.unmute", intent=i), None

    # ------------------------------------------------------------ stats
    def _sysinfo(self, i: Intent, now):
        text = self.sysinfo.report(i.get("kind") or "status")
        return Reply("info", text, "sysinfo", intent=i), None

    # ------------------------------------------------------------ power
    def _power_list(self, i, now):
        plans = self.power.list()
        text = "\n".join(f"{plan.name}{' (active)' if plan.active else ''}" for plan in plans)
        return Reply("info", text or "No power plans were returned.", "power.list", intent=i), None

    def _power(self, i: Intent, now):
        want = POWER_NAMES.get(i.get("plan"))
        if want is None:
            return Reply("error", "I can switch to saver, balanced or performance.", "power.unknown", intent=i), None
        try:
            plans = self.power.list()
        except PowerError as exc:
            return Reply("unavailable", str(exc), "power.unavailable", intent=i), None
        target = next((p for p in plans if want in p.name.casefold()), None)
        if target is None:
            self.run(self.power.settings_argv())
            return Reply("info", "That plan isn't on this PC, so I opened the Windows battery settings instead.",
                         "power.settings_opened", intent=i), None
        current = next((p for p in plans if p.active), None)
        before = current.name if current else "unknown"

        def act(now2: datetime) -> Reply:
            latest = self.power.list()
            latest_current = next((p for p in latest if p.active), None)
            if latest_current != current or target.guid not in {p.guid for p in latest}:
                return Reply("refused", "Power plans changed after preview. Request the change again.", "power.stale")
            rc = self.run(self.power.set_argv(target.guid))
            if rc != 0:
                return Reply("error", f"Windows didn't change the power plan (code {rc}).", "power.failed", intent=i)
            return Reply("done", f"Power plan changed from {before} to {target.name}.", "power.changed", intent=i)

        text = (f"Change the power plan from {before} to {target.name}? "
                f"This affects this PC's power behavior. Type 'yes' to confirm.")
        return Reply("confirm", text, "power.confirm", intent=i), act
