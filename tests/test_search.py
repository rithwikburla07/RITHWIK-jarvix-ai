import types
from pathlib import PureWindowsPath

from jarvis.system import search as S


class E:
    def __init__(self, path, is_dir=False, link=False, reparse=False):
        self.path = path
        self.name = PureWindowsPath(path).name
        self._dir, self._link, self._reparse = is_dir, link, reparse

    def is_symlink(self):
        return self._link

    def is_dir(self, follow_symlinks=True):
        return self._dir

    def stat(self, follow_symlinks=True):
        return types.SimpleNamespace(st_file_attributes=S.REPARSE if self._reparse else 0)


def tree(mapping):
    def lister(folder):
        if folder not in mapping:
            raise OSError("no such folder")
        return mapping[folder]
    return lister


DOCS = "C:\\Users\\Rithwik\\Documents"


def test_finds_nested_matches_case_insensitively():
    t = tree({DOCS: [E(DOCS + "\\Report.docx"), E(DOCS + "\\notes", is_dir=True)],
              DOCS + "\\notes": [E(DOCS + "\\notes\\report_final.txt")]})
    names = sorted(h.path for h in S.search("REPORT", [DOCS], lister=t))
    assert names == [DOCS + "\\Report.docx", DOCS + "\\notes\\report_final.txt"]


def test_never_follows_links_or_junctions():
    t = tree({DOCS: [E(DOCS + "\\jump", is_dir=True, reparse=True), E(DOCS + "\\link.lnk", link=True)],
              DOCS + "\\jump": [E(DOCS + "\\jump\\secret_report.txt")]})
    assert S.search("report", [DOCS], lister=t) == []


def test_depth_and_result_limits():
    deep = {DOCS: [E(DOCS + "\\d0", is_dir=True)]}
    cur = DOCS + "\\d0"
    for i in range(1, 10):
        nxt = f"{cur}\\d{i}"
        deep[cur] = [E(nxt, is_dir=True), E(cur + f"\\match{i}.txt")]
        cur = nxt
    hits = S.search("match", [DOCS], lister=tree(deep), max_depth=3)
    assert len(hits) <= 3
    assert len(S.search("match", [DOCS], lister=tree(deep), max_results=2)) == 2


def test_rejects_short_or_huge_queries():
    for q in ("", "a", "x" * 101):
        try:
            S.search(q, [DOCS], lister=tree({}))
        except ValueError:
            continue
        raise AssertionError(f"accepted query of length {len(q)}")


def test_approved_roots_are_profile_folders_only():
    roots = S.approved_roots({"USERPROFILE": "C:\\Users\\Rithwik"})
    assert roots == ["C:\\Users\\Rithwik\\Desktop", "C:\\Users\\Rithwik\\Documents", "C:\\Users\\Rithwik\\Downloads"]


def test_extra_roots_that_are_unsafe_are_dropped():
    roots = S.approved_roots({"USERPROFILE": "C:\\Users\\Rithwik"}, extra=["C:\\Windows", "C:\\", "\\\\srv\\share"])
    assert not any(r in ("C:\\Windows", "C:\\", "\\\\srv\\share") for r in roots)
