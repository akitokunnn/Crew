"""HOME launchers: hooks only initialize, explicit installs may switch source."""
import io
import json
import os
import shlex
import shutil
import sys
import uuid
import zipfile
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

# The official distlib console launcher uses CreateProcess directly and inherits
# stdio. A PowerShell script would be blocked by the default Restricted policy;
# a .cmd shim reparses multiline arguments as commands. Provenance is next to
# the unmodified x64 stub, which is shipped with Crew (pip is not a dependency).
WINDOWS_MAIN = '''import runpy
import sys
from pathlib import Path

entry = Path(sys.argv[0]).resolve().parent.parent / "crew.py"
sys.argv[0] = str(entry)
runpy.run_path(str(entry), run_name="__main__")
'''


def _windows_launcher():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr(zipfile.ZipInfo('__main__.py', (1980, 1, 1, 0, 0, 0)), WINDOWS_MAIN)
    stub = (Path(__file__).parent / 'vendor' / 'distlib' / 't64.exe').read_bytes()
    shebang = ('#!"' + str(Path(sys.executable).resolve()) + '"\n').encode('utf-8')
    return stub + shebang + stream.getvalue()


def _write(path, text):
    # A running Windows image cannot be replaced, including `crew.exe install`
    # reinstalling its own entry. Identical native bytes need no publication.
    if isinstance(text, bytes) and path.is_file() and path.read_bytes() == text:
        return
    # Replace atomically: a failed write never leaves a truncated entry.
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        if isinstance(text, bytes):
            temporary.write_bytes(text)
        else:
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
    launchers = [(home / "crew.py", LAUNCHER), (home / "bin" / "crew.cmd", command), (home / "bin" / "crew", shell)]
    if os.name == 'nt':
        launchers.append((home / 'bin' / 'crew.exe', _windows_launcher()))
    for path, text in launchers:
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
