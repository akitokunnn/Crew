"""HOME launchers: hooks only initialize, explicit installs may switch source."""
import json
import os
import shlex
import shutil
import sys
import uuid
from pathlib import Path

from . import store

LAUNCHER = '''"""Crew launcher installed in HOME."""
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
def _write(path, text):
    # Replace atomically: a failed write never leaves a truncated entry.
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sources(home, plugin_root):
    # Validate every required source before touching any installed entry.
    if not plugin_root.is_dir():
        raise FileNotFoundError(plugin_root)
    missing = [name for name in ("blacklist.txt", "prices.json") if not (home / name).exists()]
    for name in missing:
        if not (plugin_root / "defaults" / name).is_file():
            raise FileNotFoundError(plugin_root / "defaults" / name)
    return missing


def _launchers(home, only_missing):
    (home / "bin").mkdir(exist_ok=True)
    # Preserve the native interpreter and Windows-native paths added in 0.2.x.
    windows_python = str(Path(sys.executable).resolve()).replace('%', '%%')
    command = '@"' + windows_python + '" "%~dp0..\\crew.py" %*\n'
    python = shlex.quote(Path(sys.executable).resolve().as_posix())
    entrypoint = shlex.quote((home / 'crew.py').as_posix())
    shell = '#!/bin/sh\nexec ' + python + ' ' + entrypoint + ' "$@"\n'
    for path, text in ((home / "crew.py", LAUNCHER), (home / "bin" / "crew.cmd", command),
                       (home / "bin" / "crew", shell)):
        if only_missing and path.exists():
            continue
        _write(path, text)
        if path.name == "crew":
            path.chmod(0o755)


def install(home, plugin_root, now=None):
    """Explicit install / desktop update: point HOME's CLI at plugin_root."""
    home, plugin_root = Path(home).resolve(), Path(plugin_root).resolve()
    missing = _sources(home, plugin_root)
    home.mkdir(parents=True, exist_ok=True)
    _launchers(home, only_missing=False)
    for name in missing:
        shutil.copyfile(plugin_root / "defaults" / name, home / name)
    runtime = {"plugin_root": str(plugin_root), "python": sys.executable,
               "updated_at": store.now_iso() if now is None else now}
    # runtime.json is the only source switch and is replaced last.
    _write(home / "runtime.json", json.dumps(runtime, ensure_ascii=False, indent=2) + "\n")
    return runtime


def ensure(home, plugin_root, now=None):
    """Hooks and session launches: initialize what is missing, never switch source."""
    home, plugin_root = Path(home).resolve(), Path(plugin_root).resolve()
    if not (home / "runtime.json").exists():
        return install(home, plugin_root, now=now)
    missing = _sources(home, plugin_root)
    _launchers(home, only_missing=True)
    for name in missing:
        shutil.copyfile(plugin_root / "defaults" / name, home / name)
    return None
