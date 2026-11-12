"""Reminder scheduler: one alert per due time, survives restart, flags missed ones."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from .repository import Item, Repository

log = logging.getLogger("jarvix.scheduler")


@dataclass(frozen=True)
class Alert:
    item: Item
    missed: bool        # True if it came due while Jarvix was not running / long ago


class Scheduler:
    def __init__(self, repo: Repository, on_alert: Callable[[Alert], None], *,
                 clock: Callable[[], datetime] = datetime.now, interval: float = 5.0,
                 missed_grace_seconds: int = 120):
        self.repo, self.on_alert, self.clock = repo, on_alert, clock
        self.interval, self.grace = interval, missed_grace_seconds
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def tick(self, now: Optional[datetime] = None) -> list:
        now = now or self.clock()
        fired = []
        for item in self.repo.pending_alerts(now):
            eff = item.effective_due
            if not self.repo.claim_alert(item.id, eff):   # someone else (or an earlier tick) already did
                continue
            alert = Alert(item, missed=(now - eff).total_seconds() > self.grace)
            fired.append(alert)
            try:
                self.on_alert(alert)
            except Exception as exc:
                log.error("alert callback failed: %s", type(exc).__name__)
        return fired

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="jarvix-scheduler", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                log.error("scheduler tick failed: %s", type(exc).__name__)
            self._stop.wait(self.interval)

    def stop(self, timeout: float = 5.0) -> bool:
        self._stop.set()
        t = self._thread
        if t:
            t.join(timeout)
            return not t.is_alive()
        return True
