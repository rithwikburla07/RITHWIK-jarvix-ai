"""Entry point for the compiled per-user maintenance launcher."""
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
sys.path.append(str(root / '.venv' / 'Lib' / 'site-packages'))
from jarvis.maintenance.uninstall import run

if __name__ == "__main__":
    appdata = Path(os.environ["LOCALAPPDATA"])
    expected = appdata / "Programs" / "Jarvix"
    if root != expected:
        print("Use the maintenance executable from the per-user Jarvix installation.")
        raise SystemExit(2)
    raise SystemExit(run(root, appdata / "Jarvix", sys.argv[1:]))
