"""Crew command line.

Host commands (install, start, web, autostart, menu, doctor, usage) are written
here. Every business command is generated from the operation registry
(``ops.cli``) and runs as the local user through the backend; AI sessions use
MCP, never this CLI.
"""
import argparse
import contextlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from . import board, guard, install, store
from .ops import cli as ops_cli


def claude_agents():
    # On Windows the CLI is an npm .cmd shim, which subprocess only finds through shutil.which (PATHEXT).
    exe = shutil.which("claude")
    if exe is None:
        raise FileNotFoundError("claude not found in PATH")
    result = subprocess.run([exe, "agents", "--json"], check=True, capture_output=True, text=True, encoding="utf-8")
    agents = json.loads(result.stdout)
    if not isinstance(agents, list):
        raise ValueError("claude agents --json 必须返回数组")
    for agent in agents:
        if not isinstance(agent["sessionId"], str) or not isinstance(agent["name"], str):
            raise ValueError("claude agents 返回了非法身份")
    return agents


def _default_home():
    return Path(os.environ["CREW_HOME"]) if os.environ.get("CREW_HOME") else store.default_home()


def parser():
    p = argparse.ArgumentParser(prog="crew", description="Crew 主机命令；业务操作见 crew <领域> --help")
    p.add_argument("--home", type=Path, default=_default_home())
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("install")
    c = commands.add_parser("start")
    c.add_argument("--cwd", type=Path)
    c.add_argument("--name")
    c = commands.add_parser("web")
    c.add_argument("--port", type=int, default=47811)
    c.add_argument("--open", action="store_true")
    c.add_argument("--mcp-exposure", choices=("core", "all"), default="core",
                   help="AI 会话的 Crew 工具：core 为核心工具 + crew_search（默认），all 为全部有权限的工具")
    c = commands.add_parser("autostart").add_subparsers(dest="action", required=True)
    for action in ("install", "status", "uninstall"):
        c.add_parser(action).add_argument("--json", action="store_true")
    c = commands.add_parser("menu").add_subparsers(dest="action", required=True)
    for action in ("install", "status", "uninstall"):
        c.add_parser(action)
    commands.add_parser("doctor")
    commands.add_parser("usage").add_argument("arguments", nargs=argparse.REMAINDER)
    return p


def _command(argv):
    """The first word after global options, which decides host command vs generated operation."""
    index = 0
    while index < len(argv):
        if argv[index] in ("--home", "--project", "--request-id"):
            index += 2
        elif argv[index].startswith(("--home=", "--project=", "--request-id=")) or argv[index] == "--json":
            index += 1
        else:
            break
    return index, argv[index] if index < len(argv) else None


def doctor(conn, home, cwd):
    conn.execute("SELECT 1 FROM projects").fetchall()
    rules = guard.load_blacklist(home)
    runtime = json.loads((Path(home) / "runtime.json").read_text(encoding="utf-8"))
    if not Path(runtime["plugin_root"]).is_dir():
        raise board.BoardError("runtime.json 的 plugin_root 不存在: " + runtime["plugin_root"])
    p = board.project_for_path(conn, cwd)
    return {"数据库": "正常", "黑名单规则数": len(rules), "插件目录": runtime["plugin_root"], "当前项目": p["name"] if p is not None else None}


def main(argv=None, agents_provider=claude_agents, cwd=None, plugin_root=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        index, command = _command(argv)
        if command not in ops_cli.HOST_COMMANDS and command not in (None, "-h", "--help"):
            return ops_cli.main(argv, cwd=cwd)
        if command == "usage":
            home = argparse.ArgumentParser(prog="crew", add_help=False)
            home.add_argument("--home", type=Path, default=_default_home())
            from . import usage_cli
            return usage_cli.main(argv[index + 1:], home.parse_known_args(argv[:index])[0].home)
        args = parser().parse_args(argv)
        cwd = Path.cwd() if cwd is None else Path(cwd)
        plugin_root = Path(__file__).resolve().parents[1] if plugin_root is None else Path(plugin_root)
        if args.command == "autostart":
            from . import autostart
            value = autostart.configure(args.action, args.home, plugin_root)
            print(json.dumps(value, ensure_ascii=False, indent=2) if args.json else autostart.describe(value, args.action))
        elif args.command == "menu":
            from . import menu
            print(menu.describe(menu.configure(args.action, args.home, plugin_root), args.action))
        elif args.command == "start":
            from . import startup
            value = startup.start(args.home, plugin_root, cwd if args.cwd is None else args.cwd, args.name)
            print("Crew 已启动：%s → %s" % (value["name"], value["url"]))
        elif args.command == "web":
            from . import web
            web.serve(args.home, plugin_root, port=args.port, open_browser=args.open, cwd=cwd,
                      agents_provider=agents_provider, mcp_exposure=args.mcp_exposure)
        elif args.command == "install":
            value = install.install(args.home, plugin_root)
            with contextlib.closing(store.connect(args.home)) as conn:
                board.record_event(conn, "install", detail=value)
            print(json.dumps(value, ensure_ascii=False, indent=2))
        else:
            with contextlib.closing(store.connect(args.home)) as conn:
                print(json.dumps(doctor(conn, args.home, cwd), ensure_ascii=False, indent=2))
        return 0
    except board.Conflict as exc:
        print("冲突: %s" % exc, file=sys.stderr)
        return 3
    except (board.BoardError, OSError, sqlite3.Error, subprocess.SubprocessError, ValueError, KeyError, TypeError, re.error) as exc:
        print("%s: %s" % (type(exc).__name__, str(exc).replace("\n", " ")), file=sys.stderr)
        return 1
