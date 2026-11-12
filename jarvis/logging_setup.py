"""Bounded operational logs that record keys and types instead of private content."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

LOG = logging.getLogger("jarvix")


def configure(directory):
    path = directory / "jarvix.log"
    if path.is_symlink() or (path.exists() and path.resolve() != path):
        raise OSError("Jarvix log path is redirected.")
    for handler in LOG.handlers[:]:
        handler.close()
        LOG.removeHandler(handler)
    handler = RotatingFileHandler(path, encoding="utf-8", maxBytes=128_000, backupCount=3, delay=True)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    LOG.propagate = False


def record(reply):
    LOG.info("reply=%s kind=%s", reply.key or "unknown", reply.kind)
