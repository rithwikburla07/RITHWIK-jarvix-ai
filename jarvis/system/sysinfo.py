"""Read-only system status via psutil (optional dependency) and Windows GPU lookup."""
from __future__ import annotations

import platform
import os

NO_PSUTIL = ("System stats need the psutil package. Run: "
             ".\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt")


class SysInfoError(Exception):
    pass


def _gb(n: float) -> str:
    return f"{n / 1024 ** 3:.1f}"


class SysInfo:
    def __init__(self, psutil_mod=None, gpu_fn=None):
        self._ps = psutil_mod
        self._gpu = gpu_fn or (lambda: "only available on Windows")

    def _psutil(self):
        if self._ps is None:
            try:
                import psutil
            except ImportError as exc:
                raise SysInfoError(NO_PSUTIL) from exc
            self._ps = psutil
        return self._ps

    def snapshot(self) -> dict:
        """Numerical, nonblocking metrics for the Checkpoint-5b UI monitors."""
        ps = self._psutil()
        if not getattr(self, "_cpu_primed", False):
            ps.cpu_percent(interval=None)  # prime psutil's first reading
            self._cpu_primed = True
        vm = ps.virtual_memory()
        battery = ps.sensors_battery()
        drive = (os.environ.get("SystemDrive") or "C:") + "\\"
        du = ps.disk_usage(drive)
        return {
            "cpu": ps.cpu_percent(interval=None),
            "ram": vm.percent,
            "battery": None if battery is None else {
                "percent": round(battery.percent),
                "plugged": bool(battery.power_plugged),
            },
            "storage": {
                "percent": du.percent,
                "free_gb": round(du.free / 1024 ** 3, 1),
            },
        }

    def report(self, kind: str = "status") -> str:
        kind = (kind or "status").lower()
        if kind == "gpu":
            return f"GPU: {self._gpu()}"
        if kind == "os":
            return f"Windows: {platform.platform()}"
        if kind == "battery":
            return self._battery()
        if kind == "storage":
            return self._storage()
        if kind == "memory":
            return self._memory()
        if kind == "cpu":
            return self._cpu()
        if kind == "status":
            return "\n".join([self._cpu(), self._memory(), self._battery(), self._storage()])
        raise SysInfoError(f"unknown system report: {kind}")

    def _battery(self) -> str:
        b = self._psutil().sensors_battery()
        if b is None:
            return "No battery detected (this PC may be a desktop)."
        return f"Battery {round(b.percent)}%, {'plugged in' if b.power_plugged else 'on battery'}."

    def _storage(self) -> str:
        ps = self._psutil()
        lines = []
        for p in ps.disk_partitions(all=False):
            try:
                u = ps.disk_usage(p.mountpoint)
            except OSError:
                continue
            lines.append(f"{p.device} {_gb(u.free)} GB free of {_gb(u.total)} GB")
        return "\n".join(lines) if lines else "No drives could be read."

    def _memory(self) -> str:
        vm = self._psutil().virtual_memory()
        return f"Memory {vm.percent:.0f}% used ({_gb(vm.used)} of {_gb(vm.total)} GB)"

    def _cpu(self) -> str:
        ps = self._psutil()
        return f"CPU {ps.cpu_percent(interval=0.5):.0f}% busy, {ps.cpu_count()} logical cores"
