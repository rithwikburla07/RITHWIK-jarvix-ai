"""Approved file search and Downloads cleanup for the command engine."""
from __future__ import annotations

import ntpath
import os
from collections.abc import Callable

from ..core import fileplan
from ..core.intents import Reply
from ..core.safety import PathEnv, check_mutation_path, default_is_reparse, validate_approved_root
from .search import approved_roots, search, _list_real

MUSIC_SUFFIXES = {".mp3", ".wav", ".flac", ".m4a"}


def local_directory(path: str, env: PathEnv) -> bool:
    """Require a real local directory whose spelling doesn't hide a reparse redirect."""
    if not validate_approved_root(path, env).allowed:
        return False
    try:
        original = ntpath.normcase(ntpath.normpath(path))
        actual = ntpath.normcase(os.path.realpath(path))
        return original == actual and not default_is_reparse(path) and os.path.isdir(path)
    except OSError:
        return False


class DesktopFiles:
    def __init__(self, apps, reveal: Callable[[str], bool], settings, environ=None):
        self.apps, self.reveal, self.settings = apps, reveal, settings
        self.environ = dict(os.environ if environ is None else environ)
        self.env = PathEnv.from_environ()
        self.last_hits = []

    def roots(self):
        music = self.settings.get("music_folder")
        extra = self.settings.get("search_folders")
        if music:
            extra.append(music)
        return list(dict.fromkeys(root for root in approved_roots(self.environ, extra)
                                  if local_directory(root, self.env)))

    def _bounded_listing(self, folder):
        if local_directory(folder, self.env):
            yield from _list_real(folder)

    def find(self, intent, _now):
        query = intent.get("query", "").strip()
        wanted = intent.get("kind", "any")
        try:
            files = [] if wanted == "app" else search(query, self.roots(), lister=self._bounded_listing)
        except ValueError as exc:
            return Reply("error", str(exc), "search.invalid"), None
        hits = []
        for entry in files:
            ext = ntpath.splitext(entry.path)[1].lower()
            kind = ("Folder" if entry.is_dir else "Music" if ext in MUSIC_SUFFIXES
                    else "Shortcut" if ext == ".lnk" else "File")
            if wanted in {"any", kind.lower()} or (wanted == "file" and not entry.is_dir):
                hits.append({"label": ntpath.basename(entry.path), "path": entry.path, "kind": kind})
        if wanted in {"any", "app"} and 2 <= len(query) <= 100:
            q = query.casefold()
            for app in self.apps.entries:
                if any(q in name.casefold() for name in (app.name, *app.aliases)):
                    path = self.apps.resolve_exe(app)
                    if path:
                        hits.append({"label": app.name, "path": path, "kind": "App"})
        self.last_hits = hits[:50]
        if not self.last_hits:
            return Reply("info", "No matches in the approved locations.", "search.results", {"hits": []}), None
        result = "\n".join(f"#{index} {hit['kind']} · {hit['path']}"
                           for index, hit in enumerate(self.last_hits, 1))
        return Reply("info", result, "search.results", {"hits": self.last_hits}), None

    def open_hit(self, intent, _now):
        index = intent.get("index")
        if type(index) is not int or not 0 <= index < len(self.last_hits):
            return Reply("error", "Select a current search result.", "search.selection"), None
        hit = self.last_hits[index]
        path = hit["path"]
        if hit["kind"] == "App":
            app = self.apps.find(hit["label"])
            if app is None or self.apps.resolve_exe(app) != path:
                return Reply("error", "The application location changed. Search again.", "search.changed"), None
            self.apps.launch(app)
            return Reply("done", f"Opening {app.name}.", "app.opened"), None
        roots = self.roots()
        if not os.path.lexists(path) or not check_mutation_path(path, roots, self.env).allowed:
            return Reply("refused", "The result is no longer inside an approved folder.", "search.changed"), None
        folder = path if hit["kind"] == "Folder" else ntpath.dirname(path)
        if not local_directory(folder, self.env) or not self.reveal(folder):
            return Reply("error", "Explorer could not open the folder.", "search.failed"), None
        return Reply("done", "Opened the folder in Explorer.", "search.opened"), None

    def cleanup(self, _intent, now):
        root = ntpath.join(self.environ.get("USERPROFILE", ""), "Downloads")
        if root not in self.roots():
            return Reply("unavailable", "Downloads is unavailable or redirected.", "cleanup.root"), None
        try:
            candidates = fileplan.old_installers(root, now)
        except OSError:
            return Reply("error", "Downloads could not be read.", "cleanup.read"), None
        if len(candidates) > 100:
            return Reply("refused", "More than 100 installers match. Review Downloads in Explorer.", "cleanup.limit"), None
        plan = fileplan.build_plan("recycle", candidates, [root], self.env, now=now, dry_run=False)
        preview = fileplan.preview_text(plan)
        if not plan.items:
            return Reply("info", "No eligible installers.\n" + preview, "cleanup.empty"), None
        trasher = fileplan.default_trasher()
        if trasher is None:
            return Reply("unavailable", "Windows Recycle Bin support is unavailable.\n" + preview, "cleanup.recycle"), None

        def apply(confirmed_at):
            if not local_directory(root, self.env):
                return Reply("refused", "Downloads changed after the preview.", "cleanup.changed")
            result = fileplan.execute_plan(plan, "yes", "typed", [root], self.env,
                                           now=confirmed_at, trasher=trasher)
            if not result.items:
                return Reply("refused", f"Plan was not applied ({result.reason}).", "cleanup.refused")
            report = "\n".join(f"{item.src} · {item.detail}" for item in result.items)
            kind = "done" if all(item.ok for item in result.items) else "error"
            return Reply(kind, report, "cleanup.result")

        return Reply("confirm", "Downloads preview. Only installers older than 90 days qualify.\n" + preview,
                     "cleanup.preview"), apply
