"""Minimal test runner for machines without pytest (supports a tmp_path argument).
Usage: python tests/run_plain.py
On your laptop prefer:  python -m pytest -q
"""
import importlib, inspect, pathlib, sys, tempfile, traceback

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
passed = failed = 0
for f in sorted((ROOT / "tests").glob("test_*.py")):
    mod = importlib.import_module(f"tests.{f.stem}")
    for name, fn in inspect.getmembers(mod, inspect.isfunction):
        if not name.startswith("test_") or fn.__module__ != mod.__name__:
            continue
        try:
            if "tmp_path" in inspect.signature(fn).parameters:
                with tempfile.TemporaryDirectory() as t:
                    fn(pathlib.Path(t))
            else:
                fn()
            passed += 1
        except Exception:
            failed += 1
            print(f"FAIL {f.stem}::{name}")
            traceback.print_exc()
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
