"""Crew command line. main() accepts agent discovery for deterministic tests."""
import argparse
import contextlib
import datetime
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from . import board, channel, guard, install, store
from .hooks import git_details, git_toplevel


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


def _agents(provider, allow_failure=False):
    try:
        return provider()
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        if not allow_failure:
            raise
        print("claude agents 获取失败，cc_name 留空；%s: %s" % (type(exc).__name__, str(exc).replace("\n", " ")), file=sys.stderr)
        return None


def _cc_name(agents, session_id):
    if agents is None:
        return None
    return next((a["name"] for a in agents if a["sessionId"] == session_id), None)


def parser():
    p = argparse.ArgumentParser(prog="crew", description="Crew 共享白板")
    p.add_argument("--home", type=Path, default=store.default_home())
    p.add_argument("--session", dest="session_id")
    commands = p.add_subparsers(dest="command", required=True)

    def command(name, json_output=False):
        c = commands.add_parser(name)
        if json_output:
            c.add_argument("--json", action="store_true")
        return c

    command("install")
    c = command("init")
    c.add_argument("--name")
    c.add_argument("--root", type=Path)
    command("projects")
    c = command("resource").add_subparsers(dest="action", required=True)
    a = c.add_parser("add")
    a.add_argument("name")
    a.add_argument("--kind", choices=("device", "dir", "app", "other"), default="device")
    a.add_argument("--desc")
    a.add_argument("--guard")
    c.add_parser("list")
    c.add_parser("rm").add_argument("name")
    c = command("join")
    c.add_argument("name")
    c.add_argument("--task")
    c.add_argument("--role", choices=("member", "leader"), default="member")
    c.add_argument("--group")
    c.add_argument("--scope")
    c.add_argument("--done-when")
    command("whoami", True)
    command("task").add_argument("text")
    c = command("card", True)
    c.add_argument("--scope")
    c.add_argument("--done-when")
    c = command("status", True)
    c.add_argument("--project")
    command("who", True).add_argument("name")
    command("open").add_argument("name")
    c = command("lease").add_subparsers(dest="action", required=True)
    a = c.add_parser("take")
    a.add_argument("resource")
    a.add_argument("--for", dest="purpose", required=True)
    a.add_argument("--until")
    a.add_argument("--overwrites")
    a = c.add_parser("give")
    a.add_argument("resource")
    a.add_argument("--note")
    a.add_argument("--force", action="store_true")
    a.add_argument("--reason")
    c.add_parser("list").add_argument("--json", action="store_true")
    c = command("claim")
    c.add_argument("pattern")
    c.add_argument("--note")
    c = command("unclaim")
    a = c.add_mutually_exclusive_group(required=True)
    a.add_argument("pattern", nargs="?")
    a.add_argument("--all", action="store_true")
    command("claims", True)
    c = command("ask")
    c.add_argument("question")
    c.add_argument("--option", action="append", default=[])
    c.add_argument("--recommend")
    c.add_argument("--blocking", action="store_true")
    command("decisions", True).add_argument("--all", action="store_true")
    c = command("answer")
    c.add_argument("id", type=int)
    c.add_argument("text")
    command("withdraw").add_argument("id", type=int)
    c = command("handoff")
    c.add_argument("--to", required=True)
    c.add_argument("text")
    command("handoffs", True)
    c = command("say", True)
    c.add_argument("text")
    c.add_argument("--to", action="append", default=[])
    c = command("channel", True)
    c.add_argument("--limit", type=int, default=20)
    command("report").add_argument("text")
    c = command("reports", True)
    c.add_argument("--since", type=datetime.date.fromisoformat)
    c.add_argument("--group")
    c = command("group").add_subparsers(dest="action", required=True)
    c.add_parser("list").add_argument("--json", action="store_true")
    c = command("roster").add_subparsers(dest="action", required=True)
    for action in ("add", "remove"):
        a = c.add_parser(action)
        a.add_argument("group")
        a.add_argument("names", nargs="+")
    a = c.add_parser("move")
    a.add_argument("name")
    a.add_argument("--to", required=True)
    c = command("leader").add_subparsers(dest="action", required=True)
    a = c.add_parser("close")
    a.add_argument("group")
    a.add_argument("--with-members", action="store_true")
    a = c.add_parser("start")
    a.add_argument("--name", required=True)
    a.add_argument("--model")
    a.add_argument("--effort")
    a.add_argument("--members")
    a.add_argument("--task")
    location = a.add_mutually_exclusive_group()
    location.add_argument("--cwd", type=Path)
    location.add_argument("--new-worktree")
    a.add_argument("--dry-run", action="store_true")
    a = command("spawn", True)
    a.add_argument("--name", required=True)
    a.add_argument("--model", default=argparse.SUPPRESS)
    a.add_argument("--effort", default=argparse.SUPPRESS)
    a.add_argument("--task", required=True)
    a.add_argument("--group")
    a.add_argument("--role", choices=("member", "leader"), default="member")
    location = a.add_mutually_exclusive_group()
    location.add_argument("--cwd", type=Path)
    location.add_argument("--new-worktree")
    a.add_argument("--base")
    a.add_argument("--dry-run", action="store_true")
    a = command("wake", True)
    a.add_argument("name")
    a.add_argument("text")
    a = command("web")
    a.add_argument("--port", type=int, default=47811)
    a.add_argument("--open", action="store_true")
    c = command("autostart").add_subparsers(dest="action", required=True)
    for action in ("install", "status", "uninstall"):
        c.add_parser(action).add_argument("--json", action="store_true")
    c = command("events")
    c.add_argument("--session", dest="session_name")
    c.add_argument("--limit", type=int, default=50)
    command("doctor")
    command("usage").add_argument("arguments", nargs=argparse.REMAINDER)
    return p


LABELS = {"project": "项目", "groups": "分组", "ungrouped": "未分组", "members": "成员", "leases": "借出资源", "claims": "文件归属",
          "open_decisions": "待拍板条数", "session_id": "会话", "crew_name": "名字", "cc_name": "SendMessage 地址", "role": "角色",
          "group_name": "分组", "status": "状态", "task": "任务", "last_event_at": "最后活动", "leader": "leader", "name": "名称",
          "root": "目录", "purpose": "用途", "taken_at": "借出时间", "until_at": "预计归还", "holder_name": "借用人", "pattern": "归属路径",
          "note": "说明", "question": "问题", "answer": "答复", "body": "内容", "created_at": "创建时间", "kind": "类型", "detail": "详情",
          "scope": "负责范围", "done_when": "完成标准"}


def _human(value):
    if isinstance(value, dict):
        return "；".join("%s：%s" % (LABELS.get(k, k), _human(v)) for k, v in value.items())
    if isinstance(value, list):
        return "\n".join(_human(v) for v in value) if value else "无"
    if value is None:
        return "无"
    return str(value)


STATUS_TEXT = {"working": "干活中", "waiting": "等你", "idle": "空闲", "offline": "离线", "closed": "已收"}


def _ago(ts):
    if ts is None:
        return "-"
    seconds = (datetime.datetime.now(datetime.timezone.utc) - store.parse_iso(ts)).total_seconds()
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return "%d 分钟前" % (seconds // 60)
    if seconds < 86400:
        return "%d 小时前" % (seconds // 3600)
    return "%d 天前" % (seconds // 86400)


def _session_line(s):
    name = ("★ " if s["role"] == "leader" else "") + (s["crew_name"] or s["session_id"][:8])
    state = STATUS_TEXT[s["status"]] + ("（%s）" % s["status_note"] if s["status_note"] else "")
    return "  %s  %s  %s  地址 %s  %s" % (name, state, s["task"] or "-", s["cc_name"] or "-", _ago(s["last_event_at"]))


def inactive(s, now=None):
    """Ended sessions pile up over weeks; only live ones and named ones that ended within a day stay in view."""
    if s["status"] == "closed":
        return True
    if s["status"] != "offline":
        return False
    now = datetime.datetime.now(datetime.timezone.utc) if now is None else now
    return not s["crew_name"] or s["last_event_at"] is None or now - store.parse_iso(s["last_event_at"]) > datetime.timedelta(days=1)


def _status_text(v, now=None):
    p = v["project"]
    lines = ["%s（%s）  待拍板 %d 条" % (p["name"], p["root"], v["open_decisions"])]
    for g in v["groups"]:
        if g["closed_at"]:
            continue
        lines.append("[%s]" % g["name"])
        members = sorted(g["members"], key=lambda s: (s["role"] != "leader", s["crew_name"] or ""))
        lines.extend(_session_line(s) for s in members) if members else lines.append("  （没有成员）")
    shown = [s for s in v["ungrouped"] if not inactive(s, now)]
    if shown:
        lines.append("[未分组]")
        lines.extend(_session_line(s) for s in shown)
    if len(shown) < len(v["ungrouped"]):
        lines.append("  （另有 %d 个已结束的会话没列出：已收、未命名，或离线超过一天）" % (len(v["ungrouped"]) - len(shown)))
    lines.append("借出：" + ("无" if not v["leases"] else ""))
    lines.extend("  %s ← %s  用途 %s  %s借出  预计 %s" % (l["resource"], l["holder_name"] or l["holder_session"][:8], l["purpose"],
                                                    _ago(l["taken_at"]), l["until_at"] or "-") for l in v["leases"])
    lines.append("文件归属：" + ("无" if not v["claims"] else ""))
    lines.extend("  %s ← %s  %s" % (c["pattern"], c["crew_name"] or c["session_id"][:8], c["note"] or "") for c in v["claims"])
    return "\n".join(lines)


def _whoami_text(s):
    leader = s.get("leader")
    lines = [_session_line(s).strip(),
             "项目 %s  分组 %s  leader %s（地址 %s）" % (s["project"], s["group_name"] or "-",
                                                    leader["crew_name"] if leader else "-", leader["cc_name"] if leader else "-"),
             "worktree %s  分支 %s  会话 %s" % (s["worktree"] or "-", s["branch"] or "-", s["session_id"])]
    lines.append("负责范围：%s  完成标准：%s" % (s["scope"] if s["scope"] is not None else "-",
                                               s["done_when"] if s["done_when"] is not None else "-"))
    if s["last_message"]:
        lines.append("最后一句：" + s["last_message"])
    return "\n".join(lines)


def _emit(value, as_json=False, command=None):
    if as_json:
        print(json.dumps(value, ensure_ascii=False))
    elif value is None:
        print("完成")
    elif command == "status":
        print(_status_text(value))
    elif command == "open":
        print("已取消，会话继续保持原样" if value.get("cancelled") else "已在 VS Code 打开会话：" + value["session_id"])
    elif command == "wake":
        if "notice_id" in value:
            print(value["message"])
        else:
            print("已叫醒 %s，它在后台运行，SendMessage 地址：%s" % (value["name"], value["address"]))
    elif command in ("whoami", "who"):
        print(_whoami_text(value))
    elif command == "channel":
        blocks = []
        for message in value:
            mentions = " " + " ".join("@" + target["name"] for target in message["mentions"]) if message["mentions"] else ""
            blocks.append("%s %s%s\n%s" % (store.parse_iso(message["created_at"]).astimezone().strftime("%m-%d %H:%M"),
                                          message["speaker_name"], mentions, message["body"]))
        print("\n\n".join(blocks) if blocks else "交流区暂无发言")
    else:
        print(_human(value))


def _session_id(args):
    sid = args.session_id if args.session_id is not None else os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid:
        raise board.BoardError("请在 Claude 会话里运行，或加 --session")
    return sid


def _actor(args):
    sid = args.session_id if args.session_id is not None else os.environ.get("CLAUDE_CODE_SESSION_ID")
    return sid if sid else board.HUMAN


def _project(conn, args, cwd):
    if getattr(args, "project", None) is not None:
        return board.project(conn, args.project)["name"]
    if args.session_id is not None or os.environ.get("CLAUDE_CODE_SESSION_ID"):
        return board.session(conn, _session_id(args))["project"]
    p = board.project_for_path(conn, cwd)
    if p is None:
        raise board.BoardError("当前目录不属于已启用项目，请先 crew init")
    return p["name"]


def _usage(argv):
    # Only global options precede the command. Everything after usage belongs to its owner.
    index = 0
    while index < len(argv):
        if argv[index] in ("--home", "--session"):
            index += 2
        elif argv[index].startswith(("--home=", "--session=")):
            index += 1
        else:
            break
    if index < len(argv) and argv[index] == "usage":
        globals_parser = argparse.ArgumentParser(prog="crew")
        globals_parser.add_argument("--home", type=Path, default=store.default_home())
        globals_parser.add_argument("--session")
        args = globals_parser.parse_args(argv[:index])
        from . import usage_cli
        return True, usage_cli.main(argv[index + 1:], args.home)
    return False, None


def _with_card(conn, value):
    card = board.card(conn, value["session_id"])
    value["scope"] = None if card is None else card["scope"]
    value["done_when"] = None if card is None else card["done_when"]
    return value


def _set_card(conn, session_id, scope, done_when):
    card = board.card(conn, session_id)
    if scope is None and card is not None:
        scope = card["scope"]
    if done_when is None and card is not None:
        done_when = card["done_when"]
    board.set_card(conn, session_id, scope, done_when)
    return board.card(conn, session_id)


def execute(conn, args, cwd, agents_provider):
    cmd = args.command
    actor = _actor(args)
    if cmd == "init":
        root = args.root.resolve() if args.root is not None else Path(git_toplevel(cwd))
        if not root.is_dir():
            raise board.BoardError("项目目录不存在: " + str(root))
        return board.init_project(conn, args.name if args.name is not None else root.name, root)
    if cmd == "projects":
        return board.projects(conn)
    if cmd == "join":
        p = board.project_for_path(conn, cwd)
        if p is None:
            raise board.BoardError("当前目录不属于已启用项目，请先 crew init")
        sid = _session_id(args)
        worktree, branch = git_details(cwd)
        agents = _agents(agents_provider, allow_failure=True)
        value = board.join(conn, sid, p["name"], args.name, cwd, worktree, branch, args.task, args.role, args.group, _cc_name(agents, sid))
        if args.scope is not None or args.done_when is not None:
            _set_card(conn, sid, args.scope, args.done_when)
        value = _with_card(conn, value)
        leader = board.leader_of(conn, sid)
        if leader is not None:
            board.queue_leader_notice(conn, leader["session_id"],
                                      "%s 用 crew join 加入你组，负责：%s。请用 SendMessage 欢迎它并确认分工" %
                                      (value["crew_name"], value["scope"] or "未写"), "crew")
        return value
    if cmd == "whoami":
        return _with_card(conn, board.whoami(conn, _session_id(args)))
    if cmd == "task":
        return board.set_task(conn, _session_id(args), args.text)
    if cmd == "card":
        if args.scope is None and args.done_when is None:
            raise board.BoardError("请至少提供 --scope 或 --done-when")
        return _set_card(conn, _session_id(args), args.scope, args.done_when)
    if cmd == "answer":
        return board.answer(conn, args.id, args.text, actor)
    if cmd == "withdraw":
        return board.withdraw(conn, _session_id(args), args.id)
    if cmd == "ask":
        return board.ask(conn, _session_id(args), args.question, args.option, args.recommend, args.blocking)
    if cmd == "claim":
        return board.claim(conn, _session_id(args), args.pattern, args.note)
    if cmd == "unclaim":
        return board.unclaim(conn, _session_id(args), args.pattern)
    if cmd == "handoff":
        return board.handoff(conn, _session_id(args), args.to, args.text)
    if cmd == "handoffs":
        return board.handoffs(conn, _session_id(args))
    if cmd == "report":
        return board.report(conn, _session_id(args), args.text)
    if cmd == "say":
        speaker = conn.execute("SELECT project FROM sessions WHERE session_id=?", (actor,)).fetchone()
        if speaker is None or speaker["project"] is None:
            raise board.BoardError("交流区只给 leader 用；有事告诉你的 leader")
        return channel.post(conn, speaker["project"], actor, args.text, args.to)
    p = _project(conn, args, cwd)
    if cmd == "channel":
        return channel.read(conn, p, actor, args.limit)
    if cmd == "resource":
        if args.action == "add":
            return board.add_resource(conn, p, args.name, args.kind, args.desc, args.guard, actor)
        if args.action == "rm":
            return board.remove_resource(conn, p, args.name, actor)
        return board.resources(conn, p)
    if cmd == "status":
        return board.status(conn, p, _agents(agents_provider))
    if cmd == "open":
        from . import open_session
        return open_session.cli_open(conn, args.home, board.named_session(conn, p, args.name)["session_id"], agents_provider)
    if cmd == "who":
        s = board.named_session(conn, p, args.name)
        agents = _agents(agents_provider, allow_failure=True)
        return _with_card(conn, board.update_cc_name(conn, s["session_id"], _cc_name(agents, s["session_id"])))
    if cmd == "lease":
        if args.action == "list":
            return board.leases(conn, p)
        if args.action == "take":
            if args.until is not None:
                if re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", args.until) is None:
                    raise board.BoardError("--until 必须为 HH:MM")
            return board.take(conn, _session_id(args), args.resource, args.purpose, args.until, args.overwrites)
        if not args.force:
            actor = _session_id(args)
        return board.give(conn, p, args.resource, actor, args.note, args.force, args.reason)
    if cmd == "claims":
        return board.claims(conn, p)
    if cmd == "decisions":
        return board.decisions(conn, p, args.all)
    if cmd == "reports":
        return board.reports(conn, p, args.since.isoformat() if args.since is not None else None, args.group)
    if cmd == "group":
        return board.groups(conn, p)
    if cmd == "roster":
        return board.roster(conn, p, args.action, args.to if args.action == "move" else args.group, [args.name] if args.action == "move" else args.names, actor)
    if cmd == "leader":
        return board.close_group(conn, p, args.group, args.with_members, actor)
    if cmd == "events":
        if args.limit < 1:
            raise board.BoardError("--limit 必须大于 0")
        return board.events(conn, p, args.session_name, args.limit)
    raise board.BoardError("未知命令: " + cmd)


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
        routed, code = _usage(argv)
        if routed:
            return code
        args = parser().parse_args(argv)
        cwd = Path.cwd() if cwd is None else Path(cwd)
        plugin_root = Path(__file__).resolve().parents[1] if plugin_root is None else Path(plugin_root)
        if args.command == "autostart":
            from . import autostart
            value = autostart.configure(args.action, args.home, plugin_root)
            print(json.dumps(value, ensure_ascii=False, indent=2) if args.json else autostart.describe(value, args.action))
            return 0
        if args.command == "spawn" or (args.command == "leader" and args.action == "start"):
            from . import spawn
            value = spawn.cli_dispatch(args, cwd, plugin_root)
            if args.dry_run:
                print(json.dumps(value, ensure_ascii=False, indent=2))
            else:
                _emit(value, getattr(args, "json", False), args.command)
            return 0
        if args.command == "web":
            from . import web
            web.serve(args.home, plugin_root, port=args.port, open_browser=args.open, cwd=cwd,
                      agents_provider=agents_provider)
            return 0
        if args.command == "wake":
            from . import spawn
            with contextlib.closing(store.connect(args.home)) as conn:
                project = _project(conn, args, cwd)
            value = spawn.request_start(args.home, project, "/api/wake", {
                "name": args.name, "text": args.text,
                "caller_session_id": None if _actor(args) == board.HUMAN else _actor(args)})
            _emit(value, args.json, args.command)
            return 0
        if args.command == "install":
            value = install.install(args.home, plugin_root)
            with contextlib.closing(store.connect(args.home)) as conn:
                board.record_event(conn, "install", session_id=_actor(args), detail=value)
        else:
            with contextlib.closing(store.connect(args.home)) as conn:
                value = doctor(conn, args.home, cwd) if args.command == "doctor" else execute(conn, args, cwd, agents_provider)
        _emit(value, getattr(args, "json", False), args.command)
        return 0
    except board.Conflict as exc:
        print("冲突: %s" % exc, file=sys.stderr)
        return 3
    except (board.BoardError, OSError, sqlite3.Error, subprocess.SubprocessError, ValueError, KeyError, TypeError, re.error) as exc:
        print("%s: %s" % (type(exc).__name__, str(exc).replace("\n", " ")), file=sys.stderr)
        return 1
