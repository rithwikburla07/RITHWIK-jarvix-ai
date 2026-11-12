from datetime import datetime
from jarvis.core.parser import Parser, default_catalog

NOW = datetime(2026, 11, 12, 10, 0)  # Thursday, fixed for deterministic tests


def make_parser():
    cat = default_catalog()
    cat.contacts = {"Mom": ["mother", "mum"], "Dad": ["father"], "Alice Johnson": ["alice"], "Alex Smith": ["alex"]}
    cat.__post_init__()
    return Parser(cat)


def raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc:
        return True
    return False
