"""Rule-based command parser (no network, no API key, no Qt).

Turns text from the typed bar or speech into an `Intent`. It never executes
anything. Quoted/dictated message text is sliced from the ORIGINAL string so
it stays exactly as typed. Non-English verbs arrive with the i18n checkpoint.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ..tasks import datetime_parser as dtp
from .intents import Intent
from ..i18n.locale import Locale

I = re.I
MAX_INPUT = 2000

EXAMPLES = [
    "Open Notepad", "Volume 50", "Remind me to call Mom tomorrow at 7 PM",
    "Show my tasks for today", "Start Work mode", "Search the web for Python dataclasses",
    "What's my battery?", "Message Mom: I'm on my way",
]

DEFAULT_SITES = {
    "youtube": "https://www.youtube.com", "google": "https://www.google.com",
    "gmail": "https://mail.google.com", "github": "https://github.com",
    "wikipedia": "https://www.wikipedia.org",
}


# ------------------------------------------------------------ name matching
def _key(s: str) -> str:
    s = (s or "").lower().strip()
    s = re.sub(r"^(?:the|my|a)\s+", "", s)
    s = re.sub(r"\s+(?:app|application|program|folder|mode|workflow)$", "", s)
    return re.sub(r"\s+", " ", s)


def build_index(names: dict) -> dict:
    idx: dict = {}
    for canon, aliases in names.items():
        for n in [canon, *aliases]:
            k = _key(n)
            idx.setdefault(k, canon)
            idx.setdefault(k.replace(" ", ""), canon)
    return idx


def resolve(query: str, idx: dict) -> list:
    """Return canonical names matching `query` (1 = clear, >1 = ambiguous, 0 = none)."""
    k = _key(query)
    if not k:
        return []
    for cand in (k, k.replace(" ", "")):
        if cand in idx:
            return [idx[cand]]
    scored = []
    for key in idx:
        r = difflib.SequenceMatcher(None, k, key).ratio()
        if r >= 0.78:
            scored.append((r, idx[key]))
    scored.sort(reverse=True)
    canon: list = []
    for r, c in scored:
        if c not in canon:
            canon.append(c)
    if len(canon) > 1:
        top = scored[0][0]
        close = [c for r, c in scored if top - r < 0.05]
        out: list = []
        for c in close:
            if c not in out:
                out.append(c)
        return out
    return canon


@dataclass
class Catalog:
    apps: dict = field(default_factory=dict)       # canonical -> aliases
    contacts: dict = field(default_factory=dict)   # canonical -> aliases
    workflows: list = field(default_factory=list)
    sites: dict = field(default_factory=lambda: dict(DEFAULT_SITES))

    def __post_init__(self):
        self.app_idx = build_index(self.apps)
        self.contact_idx = build_index(self.contacts)
        self.workflow_idx = build_index({w: [] for w in self.workflows})
        self.site_idx = build_index({s: [] for s in self.sites})


# ------------------------------------------------------------ text cleaning
_WAKE = re.compile(r"^\s*(?:(?:hey|ok|okay|hi|hello)\s+)?(?:jarvix|jarvis)\b[\s,:;.!-]*", I)
_POLITE = re.compile(
    r"^(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?(?:kindly\s+)?"
    r"(?:i\s+(?:want|need|would\s+like)\s+(?:you\s+)?to\s+)?(?:go\s+ahead\s+and\s+)?(?:please\s+)?", I)
_TRAIL = re.compile(r"[\s,]*(?:please|for\s+me|thanks|thank\s+you)?[\s.!?,;]*$", I)
_QMAP = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"'})
_Q = r"""(?:"(?P<q1>.+)"|'(?P<q2>.+)')"""


def _title_clean(s: str) -> str:
    s = s.strip(" ,.;:-")
    for _ in range(3):
        s = re.sub(r"^(?:to|that|about)\s+", "", s, flags=I)
    s = re.sub(r"(?:\s+(?:to|at|on|for|by|and|in|this|next))+$", "", s, flags=I)
    return s.strip(" ,.;:-")


def _is_cancel(plain: str) -> bool:
    return bool(re.fullmatch(r"(?:cancel(?:\s+(?:that|it|this))?|never\s*mind|forget\s+it|stop\s+that|abort)", plain, I))


class Parser:
    def __init__(self, catalog: Optional[Catalog] = None):
        self.cat = catalog or Catalog()
        self.locale = Locale()

    # public ------------------------------------------------------------
    def parse(self, text: str, now: Optional[datetime] = None) -> Intent:
        raw = text if isinstance(text, str) else ""
        try:
            result = self._parse(self.locale.command(raw[:MAX_INPUT]), now or datetime.now())
            result.raw = raw[:MAX_INPUT]
            return result
        except Exception:  # never crash on user input
            return self._unknown(raw)

    # internals ---------------------------------------------------------
    def _unknown(self, raw: str, **extra) -> Intent:
        return Intent("unknown", {"suggestions": EXAMPLES, **extra}, raw)

    def _parse(self, raw: str, now: datetime) -> Intent:
        s = raw.replace("\r", " ").replace("\n", " ")
        s = "".join(ch for ch in s if ch >= " " or ch == "\t")
        off = 0
        for _ in range(3):
            for rx in (_WAKE, _POLITE):
                m = rx.match(s[off:])
                if m:
                    off += m.end()
        core_raw = s[off:].strip()
        core = core_raw.translate(_QMAP)  # same length as core_raw
        if not core:
            return self._unknown(raw)
        end = _TRAIL.search(core).start() if _TRAIL.search(core) else len(core)
        plain_raw, plain = core_raw[:end], core[:end]

        if _is_cancel(plain):
            return Intent("cancel", {}, raw)

        ask = re.fullmatch(r"(?:ask|ask the brain|ask jarvix|brain)\s+(.+)", core_raw, I)
        if ask:
            return Intent("brain_ask", {"prompt": ask.group(1)}, raw)
        for fn in (self._whatsapp, self._phone, self._volume, self._control, self._workflow,
                   self._youtube_play, self._tasks, self._cleanup, self._power, self._sysinfo,
                   self._gmail, self._music, self._files_web, self._open_close):
            out = fn(core_raw, core, plain_raw, plain, raw, now)
            if out is not None:
                return out
        return self._unknown(raw)

    # --- helpers
    def _contact_slots(self, query: str) -> dict:
        found = resolve(query, self.cat.contact_idx)
        if not found and re.fullmatch(r"\+?[0-9 ()-]{8,25}", query.strip()):
            found = [query.strip()]
        return {"contact_query": query.strip(),
                "contact": found[0] if len(found) == 1 else None,
                "candidates": found if len(found) > 1 else []}

    # --- WhatsApp (links only)
    def _whatsapp(self, core_raw, core, plain_raw, plain, raw, now):
        def sl(m, g):
            return core_raw[m.start(g):m.end(g)].strip()

        m = re.match(r"^(?:send\s+(?:a\s+)?(?:whatsapp\s+)?(?:message|text|msg)\s+to|message|text|whatsapp|msg)"
                     r"\s+(?P<c>[^:]+?)\s*:\s*(?P<m>.+)$", core, I)
        if m:
            return Intent("whatsapp_message", {**self._contact_slots(sl(m, "c")), "message": sl(m, "m")}, raw)
        m = re.match(r"^(?:send|whatsapp)\s+(?:a\s+)?(?:whatsapp\s+)?(?:message|text|msg)\s+to\s+(?P<c>.+?)\s+"
                     r"(?:saying|that\s+says|to\s+say)\s+(?P<m>.+)$", core, I)
        if m:
            return Intent("whatsapp_message", {**self._contact_slots(sl(m, "c")), "message": sl(m, "m")}, raw)
        m = re.match(rf"^(?:send|text|message|whatsapp)\s+(?:a\s+)?(?:message\s+)?{_Q}\s+to\s+(?P<c>.+?)"
                     r"(?:\s+(?:on|via|using)\s+whatsapp)?[\s.!?]*$", core, I)
        if m:
            g = "q1" if m.group("q1") else "q2"
            return Intent("whatsapp_message", {**self._contact_slots(sl(m, "c")), "message": sl(m, g)}, raw)
        m = re.match(r"^(?:(?:video\s+)?call|phone|ring)\s+(?P<c>.+?)\s+on\s+whatsapp$", plain, I)
        if m:
            return Intent("whatsapp_call", self._contact_slots(plain_raw[m.start("c"):m.end("c")]), raw)
        if re.search(r"\bwhatsapp\b", plain, I) and re.search(
                r"\b(read|unread|check|show|automate|auto[- ]?reply|desktop)\b", plain, I):
            return Intent("whatsapp_unsupported", {}, raw)
        m = re.match(r"^(?:message|text|whatsapp)\s+(?P<c>[A-Za-z][\w .'-]{0,40})$", plain, I)
        if m:
            return Intent("whatsapp_message", {**self._contact_slots(plain_raw[m.start("c"):m.end("c")]),
                                               "message": ""}, raw)
        return None

    # --- phone companion
    def _phone(self, core_raw, core, plain_raw, plain, raw, now):
        m = re.match(r"^(?:open|launch|start)\s+(?P<a>.+?)\s+on\s+(?:my\s+)?(?:phone|mobile|android)$", plain, I)
        if m:
            return Intent("phone_open_app", {"app": plain_raw[m.start("a"):m.end("a")].strip()}, raw)
        if re.match(r"^(?:ring|find|locate|buzz)\s+my\s+(?:phone|mobile)$", plain, I):
            return Intent("phone_ring", {}, raw)
        m = re.match(r"^call\s+(?P<c>(?!.*\bwhatsapp\b).+)$", plain, I)
        if m:
            return Intent("phone_dial", {"contact_query": plain_raw[m.start("c"):m.end("c")].strip()}, raw)
        return None

    # --- volume
    def _volume(self, core_raw, core, plain_raw, plain, raw, now):
        if re.fullmatch(r"un-?mute(?:\s+(?:the\s+)?(?:volume|sound|audio|speakers?|it|everything))?", plain, I):
            return Intent("unmute", {}, raw)
        if re.fullmatch(r"mute(?:\s+(?:the\s+)?(?:volume|sound|audio|speakers?|it|everything))?", plain, I):
            return Intent("mute", {}, raw)
        m = re.fullmatch(r"(?:(?:set|change|put|make|turn|bring|increase|raise|decrease|lower|reduce|adjust)\s+)?"
                         r"(?:the\s+)?(?:(?:sound|audio|master)\s+)?volume(?:\s+(?:up|down))?\s*(?:to|at)?\s*"
                         r"(?P<n>\d{1,3})\s*(?:%|percent|per\s+cent)?", plain, I)
        if m:
            n = int(m.group("n"))
            return Intent("set_volume", {"level": min(n, 100), "clamped": n > 100}, raw)
        m = re.fullmatch(r"(?:(?:turn|bring|crank|increase|raise|decrease|lower|reduce)\s+)?(?:the\s+)?"
                         r"(?:volume|it|sound)?\s*(?P<d>up|down)?\s*(?:by\s+(?P<n>\d{1,3})\s*(?:%|percent)?)?", plain, I)
        if m and re.search(r"volume|louder|quieter|\bup\b|\bdown\b", plain, I) and re.search(
                r"\b(turn|bring|crank|increase|raise|decrease|lower|reduce|up|down|volume)\b", plain, I):
            if re.search(r"\b(down|decrease|lower|reduce)\b", plain, I):
                return Intent("volume_down", {"step": int(m.group("n") or 10)}, raw)
            if re.search(r"\b(up|increase|raise|crank)\b", plain, I):
                return Intent("volume_up", {"step": int(m.group("n") or 10)}, raw)
        if re.fullmatch(r"louder", plain, I):
            return Intent("volume_up", {"step": 10}, raw)
        if re.fullmatch(r"quieter", plain, I):
            return Intent("volume_down", {"step": 10}, raw)
        return None

    # --- Jarvix itself
    def _control(self, core_raw, core, plain_raw, plain, raw, now):
        m = re.fullmatch(r"(pause|resume|exit|quit)\s+(?:jarvix|jarvis)", plain, I)
        if m:
            return Intent({"pause": "jarvix_pause", "resume": "jarvix_resume"}.get(m.group(1).lower(), "jarvix_exit"), {}, raw)
        if re.fullmatch(r"(?:help|what\s+can\s+you\s+do|commands)", plain, I):
            return Intent("help", {}, raw)
        return None

    # --- workflows
    def _workflow(self, core_raw, core, plain_raw, plain, raw, now):
        m = re.fullmatch(r"(?:create|make|build|new)\s+(?:a\s+)?(?:new\s+)?workflow\s+(?:called\s+|named\s+)?(?P<n>.+)", plain, I)
        if m:
            return Intent("create_workflow", {"name": plain_raw[m.start("n"):m.end("n")].strip()}, raw)
        m = re.fullmatch(r"(?:start|run|launch|begin|activate|enable|open)\s+(?:the\s+|my\s+)?(?P<w>.+?)\s+(?:mode|workflow)", plain, I)
        if m:
            q = plain_raw[m.start("w"):m.end("w")]
            found = resolve(q, self.cat.workflow_idx)
            if len(found) == 1:
                return Intent("run_workflow", {"workflow": found[0]}, raw)
            return self._unknown(raw, did_you_mean=found or None, kind="workflow")
        return None

    # --- YouTube / music playback
    def _youtube_play(self, core_raw, core, plain_raw, plain, raw, now):
        m = re.fullmatch(r"(?:open\s+)?youtube(?:\s+and)?\s+(?:play|search(?:\s+for)?|find)\s+(?P<q>.+)", plain, I) or \
            re.fullmatch(r"search\s+youtube\s+for\s+(?P<q>.+)", plain, I) or \
            re.fullmatch(r"play\s+(?P<q>.+?)\s+on\s+youtube", plain, I)
        if m:
            return Intent("youtube_search", {"query": plain_raw[m.start("q"):m.end("q")].strip()}, raw)
        return None

    # --- tasks & reminders
    def _tasks(self, core_raw, core, plain_raw, plain, raw, now):
        m = re.match(r"^(?:show|list|display|see|view|what(?:'s| is| are)|tell\s+me)\b(?:\s+me)?(?:\s+(?:my|the|all|upcoming))*"
                     r"\s*(?:tasks?|reminders?|to-?dos?|agenda|schedule)\b(?P<rest>.*)$", plain, I)
        if m:
            rest = m.group("rest").lower()
            rng = "tomorrow" if "tomorrow" in rest else "week" if re.search(r"week|7\s*days|seven", rest) else "today"
            return Intent("show_tasks", {"range": rng}, raw)
        m = re.fullmatch(r"(?:(?:mark|set|flag)\s+(?:the\s+)?(?:task|reminder|item|todo)\s*#?(?P<id>\d+)\s*(?:as\s+)?"
                         r"(?:done|complete[d]?|finished)|(?:complete|finish)\s+(?:task|reminder)\s*#?(?P<id2>\d+))", plain, I)
        if m:
            return Intent("complete_item", {"id": int(m.group("id") or m.group("id2"))}, raw)
        m = re.fullmatch(r"(?:delete|remove|trash)\s+(?:the\s+|my\s+)?(?P<k>task|reminder)\s*#?(?P<id>\d+)", plain, I)
        if m:
            return Intent("delete_item", {"id": int(m.group("id")), "kind": m.group("k").lower()}, raw)
        m = re.match(r"^snooze\b(?P<rest>.*)$", plain, I)
        if m:
            rest = m.group("rest")
            idm = re.search(r"(?:task|reminder)\s*#?(\d+)", rest, I)
            minutes = dtp.parse_duration(rest)
            slots = {"id": int(idm.group(1)) if idm else None, "minutes": minutes}
            if minutes is None and re.search(r"\b(until|till|to)\b", rest, I):
                pw = dtp.parse_when(re.sub(r"(?:task|reminder)\s*#?\d+", "", rest, flags=I), now)
                slots.update(date=pw.date, time=pw.time, ambiguous_hour=pw.ambiguous_hour)
            return Intent("snooze", slots, raw)
        m = re.fullmatch(r"(?:reschedule|postpone|move)\s+(?:the\s+|my\s+)?(?:task|reminder)\s*#?(?P<id>\d+)"
                         r"(?:\s+(?:to|until|for))?\s*(?P<rest>.*)", plain, I)
        if m:
            pw = dtp.parse_when(m.group("rest"), now)
            return Intent("reschedule", {"id": int(m.group("id")), "date": pw.date, "time": pw.time,
                                         "relative_dt": pw.relative_dt, "ambiguous_hour": pw.ambiguous_hour}, raw)
        m = re.match(r"^(?:remind\s+me|set\s+(?:a\s+|an\s+)?reminder|add\s+(?:a\s+)?reminder|create\s+(?:a\s+)?reminder)\b[,:]?\s*(?P<rest>.*)$", plain_raw, I)
        if m:
            return self._build_item("create_reminder", "reminder", m.group("rest"), raw, now)
        m = re.match(r"^(?:add|create|new)\s+(?:a\s+)?(?:task|to-?do)\b[,:]?\s*(?P<rest>.*)$", plain_raw, I)
        if m:
            return self._build_item("create_task", "task", m.group("rest"), raw, now)
        return None

    def _build_item(self, name: str, kind: str, rest: str, raw: str, now: datetime) -> Intent:
        pw = dtp.parse_when(rest, now)
        return Intent(name, {"kind": kind, "title": _title_clean(pw.remainder), "date": pw.date,
                             "time": pw.time, "relative_dt": pw.relative_dt,
                             "ambiguous_hour": pw.ambiguous_hour, "invalid_time": pw.invalid}, raw)

    # --- Downloads cleanup (preview only; execution is policy-gated elsewhere)
    def _cleanup(self, core_raw, core, plain_raw, plain, raw, now):
        if re.match(r"^(?:clean|clear|tidy|sweep)(?:\s+up)?\s+(?:out\s+)?(?:my\s+)?downloads\b.*\binstallers?\b", plain, I):
            return Intent("clean_downloads", {"days": 90}, raw)
        return None

    def _power(self, core_raw, core, plain_raw, plain, raw, now):
        if re.fullmatch(r"(?:show|list|check)\s+(?:the\s+)?(?:power plans?|current power plan)", plain, I):
            return Intent("power_list", {}, raw)
        m = re.search(r"\b(power\s+saver|battery\s+saver|balanced|high\s+performance)\b", plain, I)
        if m and re.match(r"^(?:switch|change|set|turn\s+on|enable|use)\b", plain, I):
            p = m.group(1).lower()
            plan = "saver" if "saver" in p else "balanced" if "balanced" in p else "high_performance"
            return Intent("power_plan", {"plan": plan}, raw)
        return None

    def _sysinfo(self, core_raw, core, plain_raw, plain, raw, now):
        if re.fullmatch(r"(?:what'?s|what is)\s+(?:my\s+)?batter", plain, I):
            return Intent("sysinfo", {"kind": "battery"}, raw)
        q = re.match(r"^(?:what(?:'s| is| are)?|how(?:'s| is| much)?|show|check|tell\s+me|get|display)\b", plain, I) \
            or re.fullmatch(r"(?:battery|cpu|ram|memory|storage|disk|gpu)(?:\s+(?:usage|status|level|space))?", plain, I)
        if not q:
            return None
        for kind, pat in (("battery", r"\bbattery\b"), ("cpu", r"\b(?:cpu|processor)\b"),
                          ("ram", r"\b(?:ram|memory)\b"), ("storage", r"\b(?:storage|disk|drives?|space)\b"),
                          ("gpu", r"\b(?:gpu|graphics)\b"), ("os", r"\b(?:os|windows)\s+version\b")):
            if re.search(pat, plain, I):
                return Intent("sysinfo", {"kind": kind}, raw)
        return None

    def _gmail(self, core_raw, core, plain_raw, plain, raw, now):
        if re.search(r"\b(?:e-?mails?|inbox|gmail)\b", plain, I) and re.match(
                r"^(?:check|read|show|get|see|any|do\s+i\s+have|how\s+many|what(?:'s| is)|open)\b", plain, I) \
                and not re.search(r"\b(?:send|compose|write|draft\s+an)\b", plain, I):
            if re.search(r"\bdrafts?\b", plain, I):
                return Intent("gmail_drafts", {}, raw)
            if re.search(r"\b(?:latest|last|newest|recent)\b", plain, I):
                return Intent("gmail_latest", {}, raw)
            if re.fullmatch(r"open\s+gmail", plain, I):
                return None  # falls through to the website shortcut
            return Intent("gmail_unread", {}, raw)
        if re.fullmatch(r"(?:show|check|read)\s+(?:my\s+)?(?:gmail\s+)?drafts", plain, I):
            return Intent("gmail_drafts", {}, raw)
        return None

    def _music(self, core_raw, core, plain_raw, plain, raw, now):
        m = re.fullmatch(r"(pause|resume|continue|stop|skip|next|previous|prev)\s+(?:the\s+)?"
                         r"(?:music|song|track|playback|playing|playlist)", plain, I) or \
            re.fullmatch(r"(next|previous|skip)", plain, I)
        if m:
            v = m.group(1).lower()
            name = {"pause": "music_pause", "resume": "music_resume", "continue": "music_resume",
                    "stop": "music_stop", "skip": "music_next", "next": "music_next",
                    "previous": "music_previous", "prev": "music_previous"}[v]
            return Intent(name, {}, raw)
        m = re.fullmatch(r"play\s+(?P<q>.+)", plain, I)
        if m:
            return Intent("music_play", {"query": plain_raw[m.start("q"):m.end("q")].strip()}, raw)
        return None

    def _files_web(self, core_raw, core, plain_raw, plain, raw, now):
        m = re.fullmatch(r"(?:search|find|look)\s+(?:for\s+)?(?:my\s+)?"
                         r"(?P<kind>files?|documents?|folders?|apps?|shortcuts?|music)\s+"
                         r"(?:(?:named|called|for|about)\s+)?(?P<q>.+)", plain, I)
        if m:
            kind = m.group("kind").casefold().removesuffix("s")
            return Intent("file_search", {"query": plain_raw[m.start("q"):m.end("q")].strip(),
                                          "kind": "file" if kind == "document" else kind}, raw)
        m = re.fullmatch(r"(?:search|google|look\s+up|lookup|find)\s+(?:the\s+)?(?:web|internet|online|google)\s+for\s+(?P<q>.+)", plain, I) or \
            re.fullmatch(r"(?:google|search\s+for|search|look\s+up)\s+(?P<q>.+)", plain, I)
        if m:
            return Intent("web_search", {"query": plain_raw[m.start("q"):m.end("q")].strip()}, raw)
        return None

    # --- open / close
    def _open_close(self, core_raw, core, plain_raw, plain, raw, now):
        m = re.fullmatch(r"(?:force\s+close|force\s+quit|kill|terminate)\s+(?P<a>.+)", plain, I)
        if m:
            return Intent("force_close_refused", {"app": plain_raw[m.start("a"):m.end("a")]}, raw)
        m = re.fullmatch(r"(?:open|go\s+to|visit)\s+(?P<u>https?://\S+)", plain, I)
        if m:
            return Intent("open_url", {"url": plain_raw[m.start("u"):m.end("u")]}, raw)
        m = re.fullmatch(r"(?:open|show)\s+(?:my\s+|the\s+)?(?P<f>desktop|documents|downloads|music)(?:\s+folder)?", plain, I)
        if m:
            return Intent("open_folder", {"folder": m.group("f").lower()}, raw)
        m = re.fullmatch(r"(?:open|launch|start|run|fire\s+up|boot\s+up|bring\s+up)\s+(?:up\s+)?(?:the\s+)?(?P<a>.+?)"
                         r"(?:\s+(?:app|application|program))?", plain, I)
        if m:
            q = plain_raw[m.start("a"):m.end("a")]
            found = resolve(q, self.cat.app_idx)
            if len(found) == 1:
                return Intent("open_app", {"app": found[0]}, raw)
            if len(found) > 1:
                return Intent("open_app", {"app": None, "candidates": found, "query": q}, raw)
            site = resolve(q, self.cat.site_idx)
            if len(site) == 1:
                return Intent("open_site", {"site": site[0], "url": self.cat.sites[site[0]]}, raw)
            wf = resolve(q, self.cat.workflow_idx)
            if len(wf) == 1:
                return Intent("run_workflow", {"workflow": wf[0]}, raw)
            return self._unknown(raw, did_you_mean=None, kind="app", query=q)
        m = re.fullmatch(r"(?:close|quit|exit|shut\s+down)\s+(?:the\s+)?(?P<a>.+?)(?:\s+(?:app|application|program|window))?", plain, I)
        if m:
            q = plain_raw[m.start("a"):m.end("a")]
            found = resolve(q, self.cat.app_idx)
            if len(found) == 1:
                return Intent("close_app", {"app": found[0]}, raw)
            return self._unknown(raw, kind="app", query=q, did_you_mean=found or None)
        return None


def default_catalog() -> Catalog:
    """Apps/contacts/workflows used when no data files are loaded (tests, first run)."""
    apps = {
        "Notepad": ["note pad", "text editor"], "Calculator": ["calc"], "Chrome": ["google chrome"],
        "Edge": ["microsoft edge"], "Explorer": ["file explorer", "files"], "Paint": ["ms paint"],
        "Command Prompt": ["cmd", "command line"], "PowerShell": ["powershell", "terminal"],
        "Settings": ["windows settings"], "Task Manager": ["taskmgr"], "VS Code": ["vscode", "visual studio code", "code"],
        "Word": ["microsoft word", "ms word"], "Excel": ["microsoft excel"], "PowerPoint": ["microsoft powerpoint", "powerpoint"],
    }
    flows = ["Work", "Focus", "Study", "Research", "Planning", "Creative", "Morning", "Email",
             "Meeting", "Coding", "Music", "Finance"]
    return Catalog(apps=apps, workflows=flows)
