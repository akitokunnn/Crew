"""Steps of a desktop update to the Crew version bundled with this desktop.

The only target is the desktop's own plugin_root; nothing is downloaded. Each
step prints one JSON result for the desktop transaction and fails explicitly.
"""
import argparse
import fnmatch
import hashlib
import json
import os
import socket
import sqlite3
import sys
import time
import uuid
from contextlib import closing
from pathlib import Path

from . import install, store
from .service_lock import HomeServiceLock

PLUGIN_ID = "crew@crew"


class UpdateError(Exception):
    pass


def update_lock(home):
    lock = HomeServiceLock(home)
    lock.path = Path(home) / "desktop" / "update.lock"
    return lock


def update_lock_held(home):
    lock = update_lock(home)
    if lock.acquire():
        lock.release()
        return False
    return True


def _excluded(relative):
    # Mirror the desktop bundle filter, plus the materialized backend marker.
    parts = relative.parts
    return (parts[0] == "tests" or (len(parts) > 1 and fnmatch.fnmatchcase(parts[0], "*-test-*"))
            or "__pycache__" in parts or relative.suffix == ".pyc" or relative.as_posix() == ".ready")


def digest(root):
    """Content identity of a plugin's production files."""
    root = Path(root)
    value = hashlib.sha256()
    files = sorted((path.relative_to(root) for path in root.rglob("*") if path.is_file()), key=lambda item: item.as_posix())
    for relative in files:
        if _excluded(relative):
            continue
        content = (root / relative).read_bytes()
        value.update(("%s\0%d\0" % (relative.as_posix(), len(content))).encode("utf-8"))
        value.update(content)
    return value.hexdigest()


def manifest_version(root):
    value = json.loads((Path(root) / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    if not isinstance(value, str) or not value:
        raise UpdateError("插件清单缺少版本：" + str(root))
    return value


def target_info(root):
    root = Path(root).resolve()
    for required in ("crew/__init__.py", "crew/cli.py", "crew_hook.py", "crew_desktop.py", "hooks/hooks.json",
                     ".claude-plugin/plugin.json", "defaults/blacklist.txt", "defaults/prices.json"):
        if not (root / required).is_file():
            raise UpdateError("桌面自带的 Crew 缺少必需文件：" + str(root / required))
    return {"root": str(root), "version": manifest_version(root), "digest": digest(root)}


def cli_entry(home):
    path = Path(home).resolve() / "runtime.json"
    if not path.exists():
        return {"state": "missing", "plugin_root": None, "version": None}
    root = Path(json.loads(path.read_text(encoding="utf-8"))["plugin_root"])
    if not (root / ".claude-plugin" / "plugin.json").is_file():
        return {"state": "broken", "plugin_root": str(root), "version": None}
    return {"state": "present", "plugin_root": str(root.resolve()), "version": manifest_version(root)}


def backup(home):
    """SQLite online backup, then an integrity check of the copy."""
    home = Path(home).resolve()
    source = store.db_path(home)
    if not source.is_file():
        raise UpdateError("没有找到 Crew 数据库：" + str(source))
    # The single backup copy: a verified new copy replaces the previous one.
    path = home / "backups" / "crew.db"
    path.parent.mkdir(exist_ok=True)
    staged = path.with_name("crew.db.%s.tmp" % uuid.uuid4().hex)
    try:
        with closing(sqlite3.connect(source, timeout=30)) as origin, closing(sqlite3.connect(staged)) as copy:
            origin.backup(copy)
        with closing(sqlite3.connect(staged)) as copy:
            checked = [row[0] for row in copy.execute("PRAGMA integrity_check")]
        if checked != ["ok"]:
            raise UpdateError("备份完整性检查失败：%s" % "; ".join(map(str, checked))[:500])
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)
    return {"path": str(path), "bytes": path.stat().st_size, "integrity": "ok"}


def switch(home, root):
    target = target_info(root)
    home = Path(home).resolve()
    from .home_migration import reset_deployment
    deployment = reset_deployment({'home': str(Path(home).resolve())})
    runtime = install.install(home, target["root"])
    return {"target": target, "runtime": runtime, "archived_deployment": deployment['archived']}


def _port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if os.name == "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def stopped(home, port, pids, timeout):
    """Wait until the old service, its administrator and managed sessions really exited."""
    from crew.supervisor import _pid_alive, _supervisor_lock
    home = Path(home).resolve()

    def free(lock):
        if lock.acquire():
            lock.release()
            return True
        return False

    deadline = time.monotonic() + timeout
    while True:
        alive = [pid for pid in pids if _pid_alive(pid) is not False]
        service, supervisor = free(HomeServiceLock(home)), free(_supervisor_lock(home))
        port_free = _port_free(port)
        if not alive and service and supervisor and port_free:
            return {"pids": pids, "port": port}
        if time.monotonic() >= deadline:
            raise UpdateError("停止超时：%s%s%s%s" % (
                "进程 %s 仍在运行；" % ", ".join(map(str, alive)) if alive else "",
                "" if service else "后台锁仍被占用；", "" if supervisor else "管理员锁仍被占用；",
                "" if port_free else "端口 %s 仍被占用；" % port))
        time.sleep(0.2)


def hold(home, stream):
    """Hold the HOME update lock until the desktop closes stdin (or crashes)."""
    lock = update_lock(home)
    if not lock.acquire():
        print(json.dumps({"ok": False, "operation": "lock", "locked": False,
                          "error": "另一个 Crew 更新正在进行"}, ensure_ascii=False), flush=True)
        return 75
    try:
        print(json.dumps({"ok": True, "operation": "lock", "locked": True}), flush=True)
        stream.read()
        return 0
    finally:
        lock.release()


def inspect(home, root):
    target = target_info(root)
    plugin = {'state': 'session-scoped', 'source': target['root'], 'source_version': target['version'],
              'registered_version': target['version'], 'scope': 'managed-session'}
    return {"target": target, "cli": cli_entry(home), "plugin": plugin}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="crew_update.py")
    parser.add_argument("--home", type=Path, required=True)
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("inspect", "switch"):
        commands.add_parser(name).add_argument("--target", type=Path, required=True)
    commands.add_parser("backup")
    commands.add_parser("lock")
    wait = commands.add_parser("stopped")
    wait.add_argument("--port", type=int, required=True)
    wait.add_argument("--pid", type=int, action="append", default=[])
    wait.add_argument("--timeout", type=float, required=True)
    args = parser.parse_args(argv)
    if args.operation == "lock":
        return hold(args.home, sys.stdin)
    try:
        if args.operation == "inspect":
            value = inspect(args.home, args.target)
        elif args.operation == "switch":
            value = switch(args.home, args.target)
        elif args.operation == "backup":
            value = backup(args.home)
        else:
            value = stopped(args.home, args.port, args.pid, args.timeout)
    except (UpdateError, OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "operation": args.operation,
                          "error": "%s: %s" % (type(exc).__name__, exc)}, ensure_ascii=False), flush=True)
        return 1
    print(json.dumps({"ok": True, "operation": args.operation, **value}, ensure_ascii=False), flush=True)
    return 0
