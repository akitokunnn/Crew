"""Hook dispatch with explicit HOME and injectable environment/git discovery."""
import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import board, guard, install, store

EVENTS = ("session-start", "user-prompt-submit", "stop", "notification", "pre-ask", "post-ask", "session-end", "guard")


def _git(cwd, *args):
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", check=True)
    return result.stdout.strip()


def git_toplevel(cwd):
    return _git(cwd, "rev-parse", "--show-toplevel")


def git_details(cwd):
    result = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
                            capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        # A project directory does not have to be a git checkout; record it without worktree / branch.
        return None, None
    return store.norm_path(result.stdout.strip()), _git(cwd, "branch", "--show-current")


def _deliveries_text(deliveries):
    lines = ["待拍板 #%s 已答复：%s" % (d["id"], d["answer"]) for d in deliveries["answers"]]
    lines.extend("交接 #%s（来自 %s）：%s" % (h["id"], h["from_session"], h["body"]) for h in deliveries["handoffs"])
    return lines


def handle(event, data, home, plugin_root, environ, git_provider=git_details, now=None):
    if event == "guard":
        reason = guard.check(home, data, now=now)
        if reason is not None:
            print(reason, file=sys.stderr)
            return 2, None
        return 0, None
    if event == "session-start":
        # Refresh HOME for every session, inside a project or not: the guard needs blacklist.txt everywhere.
        install.install(home, plugin_root, now=now)
    p = guard.enabled_project(home, data["cwd"])
    if p is None:
        return 0, None
    identity, git_info = None, None
    with contextlib.closing(store.connect(home)) as conn:
        registered = conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (data["session_id"],)).fetchone() is not None
    if event == "session-start" or not registered:
        git_info = git_provider(data["cwd"])
    if event == "session-start":
        if "CREW_NAME" in environ:
            identity = {"name": environ["CREW_NAME"], "role": environ.get("CREW_ROLE", "member"),
                        "group_name": environ.get("CREW_GROUP"), "spawn_id": environ.get("CREW_SPAWN_ID")}
    with contextlib.closing(store.connect(home)) as conn:
        deliveries = board.apply_hook(conn, event, data, p["name"], identity=identity, git_info=git_info, now=now)
        lines = _deliveries_text(deliveries)
        if event == "session-start":
            s = board.whoami(conn, data["session_id"])
            leader = s["leader"]
            lines = ["Crew 项目：%s" % p["name"], "我的 crew 名：%s" % (s["crew_name"] if s["crew_name"] is not None else "尚未认领，请运行 crew join <名字>"),
                     "分组：%s；leader：%s；SendMessage 地址：%s" % (s["group_name"], leader["crew_name"] if leader else None, leader["cc_name"] if leader else None),
                     '命令行：python "%s" ...' % (Path(home).resolve() / "crew.py"),
                     "请遵守 /crew:%s" % ("leader" if s["role"] == "leader" else "member")] + lines
    if lines and event in ("session-start", "user-prompt-submit"):
        name = "SessionStart" if event == "session-start" else "UserPromptSubmit"
        return 0, {"hookSpecificOutput": {"hookEventName": name, "additionalContext": "\n".join(lines)}}
    return 0, None


def main(argv=None):
    parser = argparse.ArgumentParser(prog="crew_hook.py")
    parser.add_argument("--home", type=Path, default=store.default_home())
    parser.add_argument("event", choices=EVENTS)
    # Hook argument errors are attendance failures too: only guard may exit 2.
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 1
    if args.event == "guard":
        try:
            data = json.loads(sys.stdin.read())
            code, output = handle(args.event, data, args.home, Path(__file__).resolve().parents[1], os.environ)
            return code
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, re.error) as exc:
            print("%s: %s. %s" % (type(exc).__name__, str(exc).replace("\n", " "), guard.TAIL), file=sys.stderr)
            return 2
    try:
        data = json.loads(sys.stdin.read())
        code, output = handle(args.event, data, args.home, Path(__file__).resolve().parents[1], os.environ)
        if output is not None:
            print(json.dumps(output, ensure_ascii=False))
        return code
    except Exception as exc:
        # DESIGN section 5 explicitly requires attendance errors to be isolated here.
        print("%s: %s" % (type(exc).__name__, str(exc).replace("\n", " ")), file=sys.stderr)
        return 1
