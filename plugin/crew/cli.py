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

from . import board, channel, guard, install, message_receipts, runtimes, store, vscode_sessions
from .hooks import git_details


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


def _default_home():
    return Path(os.environ["CREW_HOME"]) if os.environ.get("CREW_HOME") else store.default_home()


def _brief_flags(parser):
    for flag in ("title", "context", "why-user", "recommend-reason", "impact"):
        parser.add_argument("--" + flag, default=argparse.SUPPRESS)
    parser.add_argument("--consequence", action="append", default=argparse.SUPPRESS)


def _brief_arguments(args, labels, existing=None):
    fields = {"title": "title", "context": "context", "why_user": "why_user",
              "recommend_reason": "recommendation_reason", "impact": "impact"}
    if not any(hasattr(args, key) for key in (*fields, "consequence")):
        return None
    value = dict(existing or {})
    value.update({field: getattr(args, attr) for attr, field in fields.items() if hasattr(args, attr)})
    if hasattr(args, "consequence"):
        if len(labels) != len(args.consequence):
            raise board.BoardError("--option 与 --consequence 必须按顺序一一对应")
        value["options"] = [{"label": label, "consequence": consequence}
                            for label, consequence in zip(labels, args.consequence)]
    elif value.get("options") is None or [item["label"] for item in value["options"]] != labels:
        raise board.BoardError("每个 --option 都需要对应的 --consequence，说明选择后会怎样")
    value.setdefault("context", "")
    value.setdefault("impact", "")
    return value


def parser():
    p = argparse.ArgumentParser(prog="crew", description="Crew 共享白板")
    p.add_argument("--home", type=Path, default=_default_home())
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
    c = command("start")
    c.add_argument("--cwd", type=Path)
    c.add_argument("--name")
    command("projects")
    c = command("git").add_subparsers(dest="action", required=True)
    a = c.add_parser("enable")
    a.add_argument("--base")
    a.add_argument("--stable")
    a.add_argument("--runtime", choices=runtimes.RUNTIMES, default=argparse.SUPPRESS)
    c.add_parser("disable")
    c.add_parser("status")
    c.add_parser("policy").add_argument("--set", dest="policy_text")
    a = c.add_parser("request")
    a.add_argument("description")
    a.add_argument("--branch")
    c.add_parser("requests").add_argument("--all", action="store_true")
    a = c.add_parser("resolve")
    a.add_argument("id", type=int)
    a.add_argument("status", choices=("merged", "rejected", "waiting_user", "merging"))
    a.add_argument("--note", required=True)
    a.add_argument("--pr", dest="pr_number", type=int)
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
    command("compact", True).add_argument("name", nargs="?")
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
    _brief_flags(c)
    command("decisions", True).add_argument("--all", action="store_true")
    c = command("decision", True).add_subparsers(dest="action", required=True)
    a = c.add_parser("revise")
    a.add_argument("id", type=int)
    a.add_argument("--question", default=argparse.SUPPRESS)
    a.add_argument("--option", action="append", default=argparse.SUPPRESS)
    a.add_argument("--recommend", default=argparse.SUPPRESS)
    a.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    _brief_flags(a)
    trust_parser = command("trust", True)
    trust_parser.add_argument("--leader")
    c = trust_parser.add_subparsers(dest="action")
    for action in ("status", "report"):
        a = c.add_parser(action)
        a.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        a.add_argument("--leader", default=argparse.SUPPRESS)
    a = c.add_parser("set")
    a.add_argument("enabled", choices=("on", "off"))
    a.add_argument("--goal")
    a.add_argument("--leader", default=argparse.SUPPRESS)
    a.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
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
    c.add_argument("--raw", action="store_true", help="跳过要点卡，查看最近 N 条原始发言")
    command("report").add_argument("text")
    c = command("reports", True)
    c.add_argument("--since", type=datetime.date.fromisoformat)
    c.add_argument("--group")
    c = command("todo", True)
    c.add_argument("--all", action="store_true")
    todos = c.add_subparsers(dest="action")
    a = todos.add_parser("list")
    a.add_argument("--all", action="store_true", default=argparse.SUPPRESS)
    a.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    a = todos.add_parser("add")
    a.add_argument("title")
    a.add_argument("--steps", required=True)
    a.add_argument("--verify", dest="verification", required=True)
    a.add_argument("--blocking", action="store_true")
    a.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    a = todos.add_parser("withdraw")
    a.add_argument("id", type=int)
    a.add_argument("--reason", required=True)
    a.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    command("todos", True).add_argument("--all", action="store_true")
    c = command("doc", True).add_subparsers(dest="action")
    a = c.add_parser("add")
    a.add_argument("path")
    a.add_argument("--title", required=True)
    a.add_argument("--summary", required=True)
    a.add_argument("--kind", choices=("summary", "report", "guide", "other"), default="other")
    a.add_argument("--important", action="store_true")
    a.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    a = c.add_parser("list")
    a.add_argument("--leader")
    a.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    c.add_parser("refresh").add_argument("--json", action="store_true", default=argparse.SUPPRESS)
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
    a.add_argument("--model", default=argparse.SUPPRESS)
    a.add_argument("--effort", default=argparse.SUPPRESS)
    a.add_argument("--runtime", choices=runtimes.RUNTIMES, default=argparse.SUPPRESS)
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
    a.add_argument("--runtime", choices=runtimes.RUNTIMES, default=argparse.SUPPRESS)
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
    a = command('stop', True)
    a.add_argument('name')
    a = command("send", True)
    a.add_argument("name")
    body = a.add_mutually_exclusive_group(required=True)
    body.add_argument('text', nargs='?')
    body.add_argument('--text-file', type=Path, help='从 UTF-8 文件无损读取正文（含多行和引号）；- 表示标准输入。')
    a.add_argument('--kind', choices=('task','result','blocker','decision','user_answer','information','ack'), default='information',
                   help='默认 information 仅归档、不唤醒；决定用 decision，任务用 task，结果用 result，阻塞用 blocker。')
    a.add_argument('--message-id', help='调用方稳定重试编号；同一编号只用于完全相同的接收者、正文和类型。')
    for namespace, actions in (('goal', ('create','status','start','pause','resume','cancel','reopen','amend','assign','report-template','finish')),
                               ('context', ('inspect','put','correct','expire'))):
        goal_parser = command(namespace, True)
        goal_parser.set_defaults(json=True)
        goal_commands = goal_parser.add_subparsers(dest='action', required=True)
        for action in actions:
            entry = goal_commands.add_parser(action)
            if action != 'create':
                entry.add_argument('goal_id', nargs='?')
            entry.add_argument('--project')
            entry.add_argument('--execution-id')
            entry.add_argument('--json', action='store_true', default=argparse.SUPPRESS)
            payload = entry.add_mutually_exclusive_group()
            payload.add_argument('--data', help='JSON object containing action data')
            payload.add_argument('--file', type=Path, help='UTF-8 JSON action data/report file')
            for name in ('title','request','leader','to','task','text','reason','item','kind','source'):
                entry.add_argument('--' + name, default=argparse.SUPPRESS)
            entry.add_argument('--acceptance', '--accept', action='append', default=argparse.SUPPRESS)
            entry.add_argument('--constraint', action='append', default=argparse.SUPPRESS)
            entry.add_argument('--dependency', action='append', default=argparse.SUPPRESS)
            entry.add_argument('--priority', type=int, default=argparse.SUPPRESS)
            entry.add_argument('--confirmed', action='store_true', default=argparse.SUPPRESS)
            entry.add_argument('--start', action='store_true', default=argparse.SUPPRESS)
    a = command("web")
    a.add_argument("--port", type=int, default=47811)
    a.add_argument("--open", action="store_true")
    c = command("autostart").add_subparsers(dest="action", required=True)
    for action in ("install", "status", "uninstall"):
        c.add_parser(action).add_argument("--json", action="store_true")
    c = command("menu").add_subparsers(dest="action", required=True)
    for action in ("install", "status", "uninstall"):
        c.add_parser(action)
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


# Where Claude Code keeps transcripts; the VS Code session list names sessions from them.
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
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


def _context_text(s):
    context = s["context"]
    if context is None:
        return "上下文 未知（%s）" % s["context_reason"]
    return "上下文 %.0f%%（%s 万 / %s 万）" % (
        100 * context["used"] / context["max"],
        format(context["used"] / 10000, ".1f").removesuffix(".0"),
        format(context["max"] / 10000, ".1f").removesuffix(".0"))


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
        lines.extend(_session_line(s) + "  " + _context_text(s) for s in members) if members else lines.append("  （没有成员）")
    shown = [s for s in v["ungrouped"] if not inactive(s, now)]
    if shown:
        lines.append("[未分组]")
        lines.extend(_session_line(s) + "  " + _context_text(s) for s in shown)
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
    lines.append("运行端 %s  leader 运行端 %s" % (s.get("runtime", "claude"), leader.get("runtime", "claude") if leader else "-"))
    lines.append("负责范围：%s  完成标准：%s" % (s["scope"] if s["scope"] is not None else "-",
                                               s["done_when"] if s["done_when"] is not None else "-"))
    if s["last_message"]:
        lines.append("最后一句：" + s["last_message"])
    return "\n".join(lines)


def _emit(value, as_json=False, command=None, channel_card=None):
    if command == 'send' and isinstance(value, dict):
        value = message_receipts.normalize(value)
    if as_json:
        print(json.dumps(value, ensure_ascii=False))
    elif value is None:
        print("完成")
    elif command == "status":
        print(_status_text(value))
    elif command == "git":
        if "policy" in value:
            print(value["policy"] if value["policy"] is not None else "git 管理员还没写分支规则，开工前先问它")
        elif "request" in value:
            item = value["request"]
            print("合并请求 #%s · %s · %s\n%s" % (item["id"], item["status"], item["branch"], item["note"] or item["description"]))
        elif "requests" in value:
            print("\n".join("#%s · %s · %s · %s · PR %s\n%s" %
                            (item["id"], item["status"], item["branch"], item["requester_name"],
                             item["pr_number"] if item["pr_number"] is not None else "-", item["description"])
                            for item in value["requests"]) if value["requests"] else "暂无合并请求")
        elif value["enabled"]:
            print("Git 管理已启用 · base: %s · stable: %s" % (value["base"], value["stable"] or "未设置"))
        else:
            print("Git 管理未启用")
    elif command == "open":
        print("已取消，会话继续保持原样" if value.get("cancelled") else "已在 VS Code 打开会话：" + value["session_id"])
    elif command == "wake":
        if "notice_id" in value:
            print(value["message"])
        elif value.get("runtime") == "codex":
            print('已叫醒 %s，它在后台运行，消息用 crew send %s "<内容>"' % (value["name"], value["name"]))
        else:
            print("已叫醒 %s，它在后台运行，SendMessage 地址：%s" % (value["name"], value["address"]))
    elif command == "compact":
        print(value["message"] if "message" in value else "已开始压缩")
    elif command == 'stop':
        print('已停止此 Crew 身份的所有托管执行；排队消息不能自动恢复。')
    elif command == "send":
        print(value['message'])
        if value.get('message_id'):
            print('消息编号：' + value['message_id'])
    elif command == "whoami":
        print(_whoami_text(value))
    elif command == "who":
        print(_whoami_text(value) + "\n" + _context_text(value))
    elif command == "channel":
        blocks = []
        if channel_card is not None:
            blocks.append("[Crew 交流区要点]\n" + channel_card['body'])
        for message in value:
            mentions = " " + " ".join("@" + target["name"] for target in message["mentions"]) if message["mentions"] else ""
            blocks.append("%s %s%s\n%s" % (store.parse_iso(message["created_at"]).astimezone().strftime("%m-%d %H:%M"),
                                          message["speaker_name"], mentions, message["body"]))
        if channel_card is not None and not value:
            blocks.append("要点之后暂无新发言")
        print("\n\n".join(blocks) if blocks else "交流区暂无发言")
    else:
        print(_human(value))


def _session_id(args):
    sid = args.session_id if args.session_id is not None else (os.environ.get("CREW_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID"))
    if not sid:
        raise board.BoardError("请在 Claude 会话里运行，或加 --session；Codex 会话需由 Crew 绑定身份")
    return sid


def _actor(args):
    sid = args.session_id if args.session_id is not None else (os.environ.get("CREW_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID"))
    return sid if sid else board.HUMAN


def _project(conn, args, cwd):
    if getattr(args, "project", None) is not None:
        return board.project(conn, args.project)["name"]
    if args.session_id is not None or os.environ.get("CREW_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID"):
        return board.session(conn, _session_id(args))["project"]
    p = board.project_for_path(conn, cwd)
    if p is None:
        raise board.BoardError("当前目录不属于已启用项目，请先 crew init")
    return p["name"]


def _goal_command(args, cwd):
    from . import goal_actions, spawn
    payload = {}
    if args.file is not None:
        if args.file.stat().st_size > 1024 * 1024:
            raise board.BoardError('目标 JSON 文件超过 1 MiB')
        payload = json.loads(args.file.read_text(encoding='utf-8-sig'))
    elif args.data is not None:
        payload = json.loads(args.data)
    if not isinstance(payload, dict):
        raise board.BoardError('目标 data/JSON 文件必须为对象')
    action = args.action if args.command == 'goal' else 'context.' + args.action
    if action == 'finish' and 'report' not in payload:
        payload = {'report': payload}
    if action == 'finish' and isinstance(payload.get('report'), dict) and payload['report'].get('goal_id'):
        payload.setdefault('goal_id', payload['report']['goal_id'])
    for flag, field in (('title','title'),('request','request'),('leader','leader'),('to','to'),('task','task'),
                        ('text','text'),('reason','reason'),('item','item_id'),('kind','kind'),('acceptance','acceptance'),
                        ('constraint','constraints'),('dependency','dependencies'),('priority','priority'),('confirmed','confirmed'),('start','start')):
        if hasattr(args, flag):
            payload[field] = getattr(args, flag)
    if hasattr(args, 'source'):
        payload['source'] = json.loads(args.source)
    selected = getattr(args, 'goal_id', None) or os.environ.get('CREW_GOAL_ID')
    if selected:
        if payload.get('goal_id') is not None and payload['goal_id'] != selected:
            raise board.BoardError('目标编号与绑定目标冲突')
        payload['goal_id'] = selected
    execution_id = args.execution_id or os.environ.get('CREW_EXECUTION_ID')
    with contextlib.closing(store.connect(args.home)) as conn:
        project = _project(conn, args, cwd)
    payload.setdefault('project', project)
    if action in goal_actions.READ_ACTIONS:
        return goal_actions.dispatch(args.home, _actor(args), execution_id, action, payload)
    # All CLI mutations go through the existing owner process; no offline
    # fallback can accidentally launch or duplicate an execution.
    return spawn.request_start(args.home, project, '/api/goals/agent',
                               {'action': action, 'data': payload, 'actor_session_id': _actor(args), 'execution_id': execution_id})


def _goal_item_execution(conn, args, *, live=True):
    from . import goals
    execution_id = os.environ.get('CREW_EXECUTION_ID')
    if execution_id:
        execution = goals.get_execution(conn, execution_id)
        if execution['session_id'] != _session_id(args):
            raise board.BoardError('execution 与调用会话不匹配')
        if os.environ.get('CREW_GOAL_ID') and os.environ['CREW_GOAL_ID'] != execution['goal_id']:
            raise board.BoardError('执行与目标环境不匹配')
        if live:
            gate = goals.dispatch_check(conn, execution['goal_id'], execution_id=execution_id)
            if not gate['allowed']:
                raise board.Conflict(gate['reason'])
    return execution_id


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
        globals_parser.add_argument("--home", type=Path, default=_default_home())
        globals_parser.add_argument("--session")
        args = globals_parser.parse_args(argv[:index])
        from . import usage_cli
        return True, usage_cli.main(argv[index + 1:], args.home)
    return False, None


def _with_card(conn, value):
    card = board.card(conn, value["session_id"])
    value["scope"] = None if card is None else card["scope"]
    value["done_when"] = None if card is None else card["done_when"]
    value.update(runtimes.get(conn, value["session_id"]))
    value['leader'] = board.leader_of(conn, value['session_id'])
    if isinstance(value.get("leader"), dict):
        value["leader"].update(runtimes.get(conn, value["leader"]["session_id"]))
    return value


def _set_card(conn, session_id, scope, done_when):
    card = board.card(conn, session_id)
    if scope is None and card is not None:
        scope = card["scope"]
    if done_when is None and card is not None:
        done_when = card["done_when"]
    board.set_card(conn, session_id, scope, done_when)
    return board.card(conn, session_id)


def _with_context(home, project_name, sessions):
    from . import spawn
    try:
        state = spawn.request_state(home, project_name)
    except spawn.ServiceUnavailable as exc:
        for session in sessions:
            session.update(context=None, context_reason=str(exc))
        return
    people = {session["session_id"]: session for session in [*state["leaders"], *state["members"]]}
    for session in sessions:
        sid = session["session_id"]
        current = people.get(sid)
        session["context"] = current["context"] if current is not None else None
        if session.get('runtime') == 'codex' and current is not None:
            session['status'] = current['status']
            session['where'] = current['where']
        session["context_reason"] = None if session["context"] is not None else (
            "网页尚无上下文数据" if sid in people else "会话未出现在网页状态中")


def execute(conn, args, cwd, agents_provider):
    cmd = args.command
    actor = _actor(args)
    if cmd == "init":
        from . import startup
        root = args.root.resolve() if args.root is not None else startup.git_root(cwd)
        if args.root is None and not (root / '.git').exists():
            existing = board.project_for_path(conn, root)
            if existing is not None:
                if args.name is None:
                    return existing
                root = Path(existing['root'])
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
            mixed = any(runtimes.get(conn, person["session_id"])["runtime"] == "codex" for person in (value, leader))
            welcome = 'crew send %s "<欢迎与分工>" --kind task' % value["crew_name"] if mixed else "SendMessage"
            board.queue_leader_notice(conn, leader["session_id"],
                                      "%s 用 crew join 加入你组，负责：%s。请用 %s 欢迎它并确认分工" %
                                      (value["crew_name"], value["scope"] or "未写", welcome), "crew")
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
        bound = (args.session_id is not None or os.environ.get("CREW_SESSION_ID")
                 or os.environ.get("CLAUDE_CODE_SESSION_ID"))
        if bound:
            from . import trust_mode
            # Keep eligibility and answer in one transaction: a concurrent
            # archive/group close must not race this authorization check.
            with board.transaction(conn):
                sid = _session_id(args)
                session = board.session(conn, sid)
                group = conn.execute("SELECT 1 FROM groups WHERE project=? AND name=? "
                                     "AND leader_session_id=? AND closed_at IS NULL",
                                     (session["project"], session["group_name"], sid)).fetchone()
                if (session["role"] != "leader" or not session["crew_name"]
                        or session["status"] == "closed" or board.is_archived(conn, sid)
                        or group is None or trust_mode.decision_leader(conn, args.id) != sid):
                    raise board.BoardError("只能由题目原归属组中未归档、未关闭的 leader 答复")
                return board.answer.__wrapped__(conn, args.id, args.text, actor=sid)
        return board.answer(conn, args.id, args.text, actor)
    if cmd == "withdraw":
        return board.withdraw(conn, _session_id(args), args.id)
    if cmd == "ask":
        brief = _brief_arguments(args, args.option)
        if brief is None:
            print("提示：这道拍板还没有结构化摘要。请补充短标题、为什么需用户决定、推荐理由和每个选项后果；"
                  "可用 crew decision revise 原位补清，不要另发重复题。", file=sys.stderr)
        execution_id = _goal_item_execution(conn, args)
        if execution_id:
            from . import goal_links, goals
            with goals.transaction(conn):
                number = board.ask.__wrapped__(conn, _session_id(args), args.question, args.option, args.recommend, args.blocking, brief=brief)
                goal_links.bind(conn, 'decision', number, execution_id)
                return number
        return board.ask(conn, _session_id(args), args.question, args.option, args.recommend, args.blocking, brief=brief)
    if cmd == "decision":
        from . import decision_briefs
        sid = _session_id(args)
        project = _project(conn, args, cwd)
        decision = next((d for d in board.decisions(conn, project, all=True) if d["id"] == args.id), None)
        if decision is None:
            raise board.BoardError("拍板不属于当前项目或不存在")
        labels = getattr(args, "option", decision["options"])
        kwargs = {"question": getattr(args, "question", decision_briefs.INHERIT),
                  "options": getattr(args, "option", decision_briefs.INHERIT),
                  "recommendation": getattr(args, "recommend", decision_briefs.INHERIT)}
        brief = _brief_arguments(args, labels, decision_briefs.get(conn, args.id))
        if brief is not None:
            kwargs["brief"] = brief
        return decision_briefs.revise(conn, sid, args.id, **kwargs)
    if cmd == "trust":
        from . import trust_mode
        project = _project(conn, args, cwd)
        if args.leader:
            row = conn.execute('SELECT session_id,project FROM sessions WHERE session_id=?', (args.leader,)).fetchone()
            if row is not None:
                if row['project'] != project:
                    raise board.BoardError('leader 不属于当前项目')
                leader_id = row['session_id']
            else:
                leader_id = board.named_session(conn, project, args.leader)['session_id']
        elif _actor(args) != board.HUMAN:
            leader_id = trust_mode.resolve_leader(conn, _session_id(args), project)
        else:
            leader_id = None
        if not leader_id:
            raise board.BoardError('请用 --leader 指定具体 leader；成员会话可默认使用所属 leader')
        if _actor(args) != board.HUMAN and trust_mode.resolve_leader(conn, _session_id(args), project) != leader_id:
            raise board.BoardError('会话只能访问自己所属 leader 的信任设置')
        if args.action == "set":
            return trust_mode.set(conn, project, args.enabled == "on", args.goal, leader_session_id=leader_id)
        if args.action == "report":
            return trust_mode.report(conn, project, leader_id)
        return trust_mode.get(conn, project, leader_id)
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
    if cmd in ("todo", "todos"):
        from . import todo
        project = _project(conn, args, cwd)
        action = getattr(args, "action", None) or "list"
        if action == "add":
            execution_id = _goal_item_execution(conn, args)
            if execution_id:
                from . import goal_links, goals
                with goals.transaction(conn):
                    result = todo.add.__wrapped__(conn, _session_id(args), args.title, args.steps, args.verification, args.blocking)
                    goal_links.bind(conn, 'todo', result['id'], execution_id)
                    return result
            return todo.add(conn, _session_id(args), args.title, args.steps, args.verification, args.blocking)
        if action == "withdraw":
            return todo.withdraw(conn, _session_id(args), args.id, args.reason)
        return todo.list(conn, project, all=args.all)
    if cmd == "doc":
        from . import documents
        project = _project(conn, args, cwd)
        if args.action == "add":
            return documents.register(conn, _session_id(args), str((Path(cwd) / args.path).resolve()),
                                      title=args.title, summary=args.summary, kind=args.kind,
                                      important=args.important, project=project,
                                      execution_id=_goal_item_execution(conn, args))
        if args.action == "refresh":
            scan = documents.refresh(conn, args.home, project)
            return {"documents": documents.list(conn, project), "scan": scan}
        leader_name = getattr(args, "leader", None)
        leader = board.named_session(conn, project, leader_name)["session_id"] if leader_name else None
        execution_id = _goal_item_execution(conn, args, live=False)
        if execution_id:
            from . import goals
            return documents.list(conn, project, leader_session_id=leader,
                                  goal_id=goals.get_execution(conn, execution_id)['goal_id'])
        return documents.list(conn, project, leader_session_id=leader)
    if cmd == "say":
        speaker = conn.execute("SELECT project FROM sessions WHERE session_id=?", (actor,)).fetchone()
        if speaker is None or speaker["project"] is None:
            raise board.BoardError("交流区只给 leader 用；有事告诉你的 leader")
        return channel.post(conn, speaker["project"], actor, args.text, args.to)
    p = _project(conn, args, cwd)
    if cmd == "git":
        if args.action in ("enable", "disable"):
            from . import spawn
            body = {"base": args.base, "stable": args.stable} if args.action == "enable" else {}
            if hasattr(args, "runtime"):
                body["runtime"] = args.runtime
            spawn.request_start(args.home, p, "/api/git/" + args.action, body)
        elif args.action == "policy":
            return {"policy": (board.git_policy(conn, p) if args.policy_text is None else
                               board.set_git_policy(conn, p, _session_id(args), args.policy_text))}
        elif args.action == "request":
            branch = git_details(cwd)[1] if args.branch is None else args.branch
            sid = _session_id(args)
            return {"request": board.request_git_merge(conn, p, sid, branch, args.description,
                                                       title=vscode_sessions.session_title(CLAUDE_PROJECTS, sid))}
        elif args.action == "requests":
            return {"requests": board.titled_requests(board.git_requests(conn, p, args.all),
                                                      lambda sid: vscode_sessions.session_title(CLAUDE_PROJECTS, sid))}
        elif args.action == "resolve":
            return {"request": board.resolve_git_request(conn, p, _session_id(args), args.id, args.status,
                                                          args.note, args.pr_number)}
        config = board.git_config(conn, p)
        return {"enabled": config is not None, "base": None if config is None else config["base"],
                "stable": None if config is None else config["stable"]}
    if cmd == "resource":
        if args.action == "add":
            return board.add_resource(conn, p, args.name, args.kind, args.desc, args.guard, actor)
        if args.action == "rm":
            return board.remove_resource(conn, p, args.name, actor)
        return board.resources(conn, p)
    if cmd == "status":
        needs_claude = any(runtimes.get(conn, s['session_id'])['runtime'] == 'claude'
                           and s['status'] != 'closed' for s in board.sessions(conn, p))
        value = board.status(conn, p, _agents(agents_provider) if needs_claude else [])
        people = [*value["ungrouped"], *(s for g in value["groups"] for s in g["members"])]
        for person in people:
            person.update(runtimes.get(conn, person['session_id']))
        _with_context(args.home, p, people)
        return value
    if cmd == "open":
        from . import open_session
        return open_session.cli_open(conn, args.home, board.named_session(conn, p, args.name)["session_id"], agents_provider)
    if cmd == "who":
        s = board.named_session(conn, p, args.name)
        if runtimes.get(conn, s["session_id"])["runtime"] == "codex":
            value = _with_card(conn, s)
        else:
            agents = _agents(agents_provider, allow_failure=True)
            value = _with_card(conn, board.update_cc_name(conn, s["session_id"], _cc_name(agents, s["session_id"])))
        _with_context(args.home, p, [value])
        return value
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
        from . import decision_briefs
        return decision_briefs.enrich(conn, board.decisions(conn, p, args.all))
    if cmd == "reports":
        return board.reports(conn, p, args.since.isoformat() if args.since is not None else None, args.group)
    if cmd == "group":
        return board.groups(conn, p)
    if cmd == "roster":
        return board.roster(conn, p, args.action, args.to if args.action == "move" else args.group, [args.name] if args.action == "move" else args.names, actor)
    if cmd == "leader":
        from . import spawn
        return spawn.request_start(args.home, p, "/api/group/close", {"group": args.group, "with_members": args.with_members})
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
        if args.command == "menu":
            from . import menu
            print(menu.describe(menu.configure(args.action, args.home, plugin_root), args.action))
            return 0
        if args.command == "start":
            from . import startup
            value = startup.start(args.home, plugin_root, cwd if args.cwd is None else args.cwd, args.name)
            print("Crew 已启动：%s → %s" % (value["name"], value["url"]))
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
                target = board.named_session(conn, project, args.name)
                runtime = runtimes.get(conn, target["session_id"])["runtime"]
            value = spawn.request_start(args.home, project, "/api/wake", {
                "name": args.name, "text": args.text,
                "caller_session_id": None if _actor(args) == board.HUMAN else _actor(args)})
            value = {**value, "runtime": runtime}
            _emit(value, args.json, args.command)
            return 0
        if args.command == "send":
            from . import spawn
            text = args.text
            if args.text_file is not None:
                if str(args.text_file) == '-':
                    # A redirected Windows stdin may use a legacy code page
                    # and translate CRLF. Read UTF-8 bytes before either happens.
                    binary_input = getattr(sys.stdin, 'buffer', None)
                    text = (binary_input.read().decode('utf-8-sig') if binary_input is not None
                            else sys.stdin.read())
                else:
                    try:
                        with args.text_file.open('r', encoding='utf-8-sig', newline='') as message_file:
                            text = message_file.read()
                    except (OSError, UnicodeError) as exc:
                        raise board.BoardError('读取消息文件失败：%s；%s: %s' %
                                               (args.text_file, type(exc).__name__, exc)) from exc
            caller = _session_id(args)
            with contextlib.closing(store.connect(args.home)) as conn:
                project = _project(conn, args, cwd)
            value = spawn.request_start(args.home, project, "/api/messages", {
                "name": args.name, "text": text, "caller_session_id": caller,
                'execution_id': os.environ.get('CREW_EXECUTION_ID'), 'kind': args.kind,
                **({'message_id': args.message_id} if args.message_id else {})})
            _emit(value, args.json, args.command)
            return 0
        if args.command in ('goal', 'context'):
            value = _goal_command(args, cwd)
            _emit(value, True, args.command)
            return 0
        if args.command == 'stop':
            from . import spawn
            with contextlib.closing(store.connect(args.home)) as conn:
                project = _project(conn, args, cwd)
                row = conn.execute('SELECT session_id FROM sessions WHERE project=? AND session_id=?', (project, args.name)).fetchone()
                target = row['session_id'] if row else board.named_session(conn, project, args.name)['session_id']
            value = spawn.request_start(args.home, project, '/api/session/stop', {
                'session_id': target, 'caller_session_id': _actor(args)})
            _emit(value, args.json, args.command)
            return 0
        if args.command == "compact":
            from . import spawn
            with contextlib.closing(store.connect(args.home)) as conn:
                project = _project(conn, args, cwd)
                sid = _session_id(args) if args.name is None else board.named_session(conn, project, args.name)["session_id"]
            value = spawn.request_start(args.home, project, "/api/session/compact", {
                "session_id": sid, "caller_session_id": _actor(args)})
            _emit(value, args.json, args.command)
            return 0
        if args.command == "channel":
            with contextlib.closing(store.connect(args.home)) as conn:
                project = _project(conn, args, cwd)
                card = None if args.raw else channel.digest(conn, project)
                value = channel.read(conn, project, _actor(args), args.limit,
                                     after=card['upto_message_id'] if card is not None else 0)
            _emit(value, args.json, args.command, channel_card=card)
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
