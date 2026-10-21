"""Install the explicit HOME launchers and missing default files."""
import json
import shutil
import sys
from pathlib import Path

from . import store


def install(home, plugin_root, now=None):
    home, plugin_root = Path(home).resolve(), Path(plugin_root).resolve()
    # Validate required sources before replacing any installed launchers.
    missing = [name for name in ("blacklist.txt", "prices.json") if not (home / name).exists()]
    for name in missing:
        if not (plugin_root / "defaults" / name).is_file():
            raise FileNotFoundError(plugin_root / "defaults" / name)
    home.mkdir(parents=True, exist_ok=True)
    (home / "bin").mkdir(exist_ok=True)
    runtime = {"plugin_root": str(plugin_root), "python": sys.executable,
               "updated_at": store.now_iso() if now is None else now}
    (home / "runtime.json").write_text(json.dumps(runtime, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    launcher = '''"""Crew launcher installed in HOME."""
import json
import sys
from pathlib import Path

home = Path(__file__).resolve().parent
runtime = json.loads((home / "runtime.json").read_text(encoding="utf-8"))
sys.path.insert(0, runtime["plugin_root"])
from crew.cli import main

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
raise SystemExit(main(["--home", str(home), *sys.argv[1:]]))
'''
    (home / "crew.py").write_text(launcher, encoding="utf-8")
    (home / "bin" / "crew.cmd").write_text('@python "%USERPROFILE%\\.crew\\crew.py" %*\n', encoding="utf-8")
    for name in missing:
        shutil.copyfile(plugin_root / "defaults" / name, home / name)
    return runtime
