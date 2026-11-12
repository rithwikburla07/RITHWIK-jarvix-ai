"""Read supported Start Menu shortcuts and persist explicit executable approvals."""
from __future__ import annotations

import json
import ntpath
import os
import re
import subprocess
from dataclasses import replace
from pathlib import Path

from ..config.settings import read_json, write_json
from ..core.intents import Reply
from ..core.safety import default_is_reparse
from .apps import AppEntry

_RESOLVE = """
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
$shell = New-Object -ComObject WScript.Shell
$records = foreach ($path in (ConvertFrom-Json -InputObject ([Console]::In.ReadToEnd()))) {
    try {
        $shortcut = $shell.CreateShortcut($path)
        @{name = [IO.Path]::GetFileNameWithoutExtension($path); target = $shortcut.TargetPath; args = $shortcut.Arguments}
    } catch {}
}
ConvertTo-Json -InputObject @($records) -Compress
"""
_ALIAS = re.compile(r"[\w .+-]{1,48}", re.UNICODE)
_MAX_LINKS = 240


class AppApprovals:
    def __init__(self, registry, catalog, path):
        self.registry, self.catalog = registry, catalog
        self.path = Path(path)
        self.discovered = []
        self.custom = {}
        self.aliases = {}
        self.original = list(registry.entries)
        self.original_aliases = dict(catalog.apps)
        self.problem = ""
        try:
            record = read_json(self.path)
            custom = record.get("apps", [])
            aliases = record.get("aliases", {})
            if not isinstance(custom, list) or not isinstance(aliases, dict):
                raise ValueError("Invalid app approvals.")
            for item in custom[:80]:
                name, target = item.get("name"), item.get("target")
                if isinstance(name, str) and _ALIAS.fullmatch(name) and self.valid_target(target):
                    self.custom[name] = target
            self.aliases = {key: value for key, value in aliases.items()
                            if isinstance(value, str) and _ALIAS.fullmatch(value)}
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError):
            self.problem = "App approvals could not be loaded."
        self._refresh()

    def _refresh(self):
        original_names = {entry.name.casefold() for entry in self.original}
        entries = self.original + [AppEntry(name, (), ntpath.basename(target), (target,), closable=False,
                                           approved_target=target)
                                   for name, target in self.custom.items()
                                   if name.casefold() not in original_names]
        registry_names = {entry.name: list(self.original_aliases.get(entry.name, ())) for entry in entries}
        self.registry.entries = [
            replace(entry, aliases=tuple(dict.fromkeys(
                (*entry.aliases, *registry_names[entry.name], *([self.aliases[entry.name]]
                                                              if entry.name in self.aliases else [])))))
            for entry in entries
        ]
        self.catalog.apps = {entry.name: list(entry.aliases) for entry in self.registry.entries}
        self.catalog.__post_init__()

    def valid_target(self, target):
        if not isinstance(target, str) or not target or target.startswith("\\\\"):
            return False
        norm = ntpath.normcase(ntpath.normpath(target))
        if ntpath.splitext(norm)[1] != ".exe" or not ntpath.isabs(norm):
            return False
        env = self.registry.env
        locations = [env.get("ProgramFiles"), env.get("ProgramFiles(x86)"),
                     ntpath.join(env["LOCALAPPDATA"], "Programs") if env.get("LOCALAPPDATA") else None]
        try:
            if not self.registry.isfile(target) or ntpath.normcase(os.path.realpath(target)) != norm:
                return False
            return any(norm.startswith(ntpath.normcase(p).rstrip("\\") + "\\")
                       for p in locations if p)
        except OSError:
            return False

    def _links(self):
        env = self.registry.env
        paths = []
        for base in (env.get("APPDATA"), env.get("ProgramData")):
            if not base:
                continue
            root = os.path.join(base, "Microsoft", "Windows", "Start Menu", "Programs")
            if default_is_reparse(root) or os.path.normcase(os.path.realpath(root)) != os.path.normcase(root):
                continue
            count = 0
            for folder, dirs, names in os.walk(root, followlinks=False):
                count += 1
                dirs[:] = [d for d in dirs if not default_is_reparse(os.path.join(folder, d))]
                for name in names:
                    if name.casefold().endswith(".lnk"):
                        candidate = os.path.join(folder, name)
                        if not default_is_reparse(candidate):
                            paths.append(candidate)
                            if len(paths) >= _MAX_LINKS:
                                return paths
                if count >= _MAX_LINKS:
                    break
        return paths

    def discover(self, _intent, _now):
        self.discovered = []
        links = self._links()
        if links:
            system = self.registry.env.get("SystemRoot", r"C:\Windows")
            executable = ntpath.join(system, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
            try:
                completed = subprocess.run(
                    [executable, "-NoProfile", "-NonInteractive", "-Command", _RESOLVE],
                    input=json.dumps(links), capture_output=True, text=True, encoding="utf-8",
                    timeout=12, shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if completed.returncode != 0:
                    raise ValueError("Shortcut reader failed.")
                records = json.loads(completed.stdout)
                known = {e.name.casefold() for e in self.registry.entries}
                targets = set()
                for rec in records:
                    name, target = rec.get("name"), rec.get("target")
                    if (not isinstance(name, str) or not _ALIAS.fullmatch(name) or name.casefold() in known
                            or rec.get("args") or not self.valid_target(target)):
                        continue
                    if target.casefold() not in targets:
                        targets.add(target.casefold())
                        self.discovered.append({"name": name, "target": target})
            except (OSError, ValueError, subprocess.SubprocessError):
                return Reply("error", "Start Menu shortcuts could not be read.", "apps.discovery_failed"), None
        return Reply("info", f"Found {len(self.discovered)} eligible Start Menu shortcuts.",
                     "apps.discovered", {"apps": self.discovered}), None

    def approve(self, intent, _now):
        idx = intent.get("index")
        if type(idx) is not int or not 0 <= idx < len(self.discovered):
            return Reply("error", "Select a current Start Menu result.", "apps.selection"), None
        entry = self.discovered[idx]

        def save(_confirmed):
            if not self.valid_target(entry["target"]):
                return Reply("refused", "The executable changed after discovery.", "apps.changed")
            if self.registry.find(entry["name"]):
                return Reply("error", "That application name is already in use.", "apps.duplicate")
            if len(self.custom) >= 80:
                return Reply("refused", "The list already has 80 custom applications.", "apps.limit")
            custom = {**self.custom, entry["name"]: entry["target"]}
            self._save(custom, self.aliases)
            return Reply("done", f"Approved {entry['name']}.", "apps.updated")

        return Reply("confirm", f"Approve {entry['name']} for launching?\n{entry['target']}\nType yes to confirm.",
                     "apps.confirm"), save

    def set_alias(self, intent, _now):
        entry = self.registry.find(intent.get("name", ""))
        alias = intent.get("alias", "").strip()
        if entry is None or (alias and not _ALIAS.fullmatch(alias)):
            return Reply("error", "Choose an approved app and a short alias.", "apps.alias"), None
        other = self.registry.find(alias)
        if other and other.name != entry.name:
            return Reply("error", "That alias is already in use.", "apps.alias"), None
        aliases = dict(self.aliases)
        aliases.pop(entry.name, None)
        if alias:
            aliases[entry.name] = alias
        self._save(self.custom, aliases)
        return Reply("done", "Application alias saved.", "apps.updated"), None

    def _save(self, custom, aliases):
        write_json(self.path, {"apps": [{"name": name, "target": target} for name, target in custom.items()],
                               "aliases": aliases})
        self.custom, self.aliases = custom, aliases
        self._refresh()
