"""Steps of a desktop update to the Crew version bundled with this desktop.

The only target is the desktop's own plugin_root; nothing is downloaded. Each
step prints one JSON result for the desktop transaction and fails explicitly.
"""
import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
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


def claude_command():
    from .native_process import claude_executable
    return [claude_executable()]


def _run(command, args):
    from .tool_updates import _run as run_tool
    timeout = 30 if args[:2] == ["plugin", "list"] else 120
    try:
        return run_tool([*command, *args], timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise UpdateError("claude %s 超过 %s 秒未完成，已停止该次命令，请重试" % (" ".join(args[:2]), timeout)) from exc


def plugin_entries(command):
    result = _run(command, ["plugin", "list", "--json"])
    if result.returncode != 0:
        raise UpdateError("claude plugin list 失败（退出码 %s）：%s" % (result.returncode, (result.stderr or result.stdout).strip()[:500]))
    entries = json.loads(result.stdout)
    if not isinstance(entries, list):
        raise UpdateError("claude plugin list --json 没有返回数组")
    return [entry for entry in entries if isinstance(entry, dict) and entry.get("id") == PLUGIN_ID]


def plugin_state(target, command):
    """Registered and actually read plugin source; readFromFolder wins over the cache."""
    entries = plugin_entries(command)
    if not entries:
        return {"state": "not_installed"}
    scopes = [str(entry.get("scope")) for entry in entries]
    if scopes != ["user"]:
        return {"state": "blocked", "scopes": scopes,
                "reason": "Claude 中的 crew 插件安装在 %s 范围；桌面只同步 user 范围的单一安装。请在对应位置手动执行 "
                          "claude plugin update crew@crew --scope <范围> 并核对版本后再试。" % "、".join(scopes)}
    entry = entries[0]
    folder = entry.get("readFromFolder")
    source = Path(folder or entry["installPath"])
    present = source.is_dir()
    info = {"scope": "user", "registered_version": entry["version"], "source": str(source),
            "read_from_folder": bool(folder), "source_version": manifest_version(source) if present else None,
            "source_present": present, "matches": present and digest(source) == target["digest"]}
    if info["matches"] and entry["version"] == target["version"]:
        return {**info, "state": "current"}
    if folder and not info["matches"]:
        return {**info, "state": "blocked",
                "reason": "Claude 插件实际读取 %s（版本 %s），内容与桌面自带的 %s（版本 %s）不同；官方 plugin update 不会改写这个目录。"
                          "请先把该目录更新为与桌面相同的版本（例如源码仓库切到对应提交），或用当前桌面版本重新安装插件，再重新检查。"
                          % (source, info["source_version"] or "未知", target["root"], target["version"])}
    if folder:
        return {**info, "state": "update"}
    return {**info, "state": "blocked",
            "reason": "Claude 插件从缓存 %s 读取，无法在更新前确认官方更新后的来源与桌面自带版本一致。请手动执行 "
                      "claude plugin update crew@crew --scope user，并确认版本为 %s 后再检查。" % (source, target["version"])}


def update_plugin(target, command):
    before = plugin_state(target, command)
    if before["state"] != "update":
        raise UpdateError("Claude 插件状态已变化（%s），请重新检查：%s" % (before["state"], before.get("reason", "")))
    result = _run(command, ["plugin", "update", PLUGIN_ID, "--scope", "user", "--json"])
    lines = [line for line in result.stdout.splitlines() if line.strip().startswith("{")]
    reply = json.loads(lines[0]) if lines else None
    if result.returncode != 0 or not isinstance(reply, dict) or reply.get("outcome") != "ok":
        detail = (reply.get("message") or reply.get("failureCode")) if isinstance(reply, dict) else None
        raise UpdateError("claude plugin update 失败（退出码 %s）：%s" % (
            result.returncode, detail or (result.stderr or result.stdout).strip()[:500] or "没有输出"))
    after = plugin_state(target, command)
    if after["state"] != "current":
        raise UpdateError("插件更新后仍与桌面自带版本不一致：登记 %s，实际读取 %s（版本 %s）。请手动执行 "
                          "claude plugin update crew@crew --scope user 并核对。" % (
                              after.get("registered_version"), after.get("source"), after.get("source_version")))
    return {"before": before, "after": after, "reply": reply}


def backup(home):
    """SQLite online backup, then an integrity check of the copy."""
    home = Path(home).resolve()
    source = store.db_path(home)
    if not source.is_file():
        raise UpdateError("没有找到 Crew 数据库：" + str(source))
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    directory = home / "backups" / ("update-%s-%s" % (stamp, uuid.uuid4().hex[:6]))
    directory.mkdir(parents=True)
    path = directory / "crew.db"
    with closing(sqlite3.connect(source, timeout=30)) as origin, closing(sqlite3.connect(path)) as copy:
        origin.backup(copy)
    with closing(sqlite3.connect(path)) as copy:
        checked = [row[0] for row in copy.execute("PRAGMA integrity_check")]
    if checked != ["ok"]:
        raise UpdateError("备份完整性检查失败：%s（备份 %s）" % ("; ".join(map(str, checked))[:500], path))
    return {"path": str(path), "bytes": path.stat().st_size, "integrity": "ok"}


def switch(home, root):
    target = target_info(root)
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
    try:
        command = claude_command()
    except FileNotFoundError as exc:
        plugin = {"state": "unavailable", "reason": "未找到 claude 命令，无法确认 Claude 插件状态：" + str(exc)}
    else:
        try:
            plugin = plugin_state(target, command)
        except (UpdateError, OSError, ValueError, KeyError, TypeError) as exc:
            plugin = {"state": "blocked", "reason": "无法读取 Claude 插件登记：%s: %s" % (type(exc).__name__, exc)}
    return {"target": target, "cli": cli_entry(home), "plugin": plugin}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="crew_update.py")
    parser.add_argument("--home", type=Path, required=True)
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("inspect", "plugin-update", "switch"):
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
        elif args.operation == "plugin-update":
            value = update_plugin(target_info(args.target), claude_command())
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
