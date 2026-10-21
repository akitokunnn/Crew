"""Prepare background Crew sessions and route CLI launches to the web service."""
import contextlib
import json
import re
import sqlite3
import subprocess
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import board, store

def command(session_id, name, role, resume=False):
    args = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--include-partial-messages", "--replay-user-messages", "--permission-mode", "bypassPermissions",
            "--name", name]
    if role == "leader":
        args += ["--agent", "crew:leader"]
    return args + ["--resume" if resume else "--session-id", session_id]


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + "\u4e0d\u80fd\u4e3a\u7a7a")
    return value


def prepare(conn, home, plugin_root, project_name, name, task, group=None, role="member",
            cwd=None, new_worktree=None, base=None, now=None):
    _text(name, "\u540d\u5b57")
    _text(task, "\u4efb\u52a1")
    if role not in ("member", "leader"):
        raise ValueError("\u89d2\u8272\u5fc5\u987b\u662f member \u6216 leader")
    board.available_name(conn, project_name, name)
    root = Path(board.project(conn, project_name)["root"]).resolve()
    if cwd is not None and new_worktree is not None:
        raise ValueError("--cwd \u4e0e --new-worktree \u4e0d\u80fd\u540c\u65f6\u4f7f\u7528")
    if base is not None and new_worktree is None:
        raise ValueError("--base \u5fc5\u987b\u4e0e --new-worktree \u4e00\u8d77\u4f7f\u7528")
    groups = {g["name"]: g for g in board.groups(conn, project_name)}
    if role == "leader":
        if group is not None and group != name:
            raise board.BoardError("leader \u5206\u7ec4\u5fc5\u987b\u4e0e crew \u540d\u540c\u540d")
        group = name
    elif group is not None and (group not in groups or groups[group]["closed_at"] is not None):
        raise board.BoardError("\u5206\u7ec4\u4e0d\u5b58\u5728\u6216\u5df2\u5173\u95ed: " + group)
    worktree_command = None
    directory = root if cwd is None else Path(cwd).resolve()
    if not store.is_under(directory, root):
        raise board.BoardError("--cwd \u5fc5\u987b\u5728\u9879\u76ee root \u4e0b")
    if not directory.is_dir():
        raise board.BoardError("\u5de5\u4f5c\u76ee\u5f55\u4e0d\u5b58\u5728: " + str(directory))
    if new_worktree is not None:
        _text(new_worktree, "\u5206\u652f")
        slug = re.sub(r"[^A-Za-z0-9_-]", "", new_worktree.split("/")[-1])
        if not slug:
            raise ValueError("\u5206\u652f\u540d\u6700\u540e\u4e00\u6bb5\u9700\u5305\u542b\u5b57\u6bcd\u3001\u6570\u5b57\u3001- \u6216 _")
        directory = (root / "Temp" / "wt" / slug).resolve()
        if not store.is_under(directory, root):
            raise board.BoardError("worktree \u76ee\u5f55\u5fc5\u987b\u5728\u9879\u76ee root \u4e0b")
        ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", "Temp/wt/"],
                                 capture_output=True, text=True, encoding="utf-8")
        if ignored.returncode == 1:
            raise board.BoardError("\u8bf7\u5148\u8ba9 git \u5ffd\u7565 Temp/wt")
        if ignored.returncode != 0:
            raise board.BoardError("git check-ignore \u5931\u8d25: " + ignored.stderr.strip())
        if directory.exists():
            raise board.BoardError("worktree \u76ee\u5f55\u5df2\u5b58\u5728: " + str(directory))
        worktree_command = ["git", "-C", str(root), "worktree", "add", "-b", new_worktree, str(directory), base if base is not None else "HEAD"]
    spawn_id, session_id = uuid.uuid4().hex, str(uuid.uuid4())
    target = Path(home).resolve() / "spawn" / spawn_id
    md_path, json_path = target.with_suffix(".md"), target.with_suffix(".json")
    leader = None
    if group in groups and groups[group]["closed_at"] is None and groups[group]["leader_session_id"] is not None:
        leader = board.session(conn, groups[group]["leader_session_id"])
    if role == "leader":
        leader_name = "\u5c31\u662f\u4f60\u81ea\u5df1"
    elif leader is not None and leader["status"] not in ("offline", "closed"):
        leader_name = leader["crew_name"]
    else:
        leader_name = "leader \u8fd8\u6ca1\u4e0a\u7ebf"
    brief = ("\u4f60\u662f Crew \u4f1a\u8bdd\uff1a%s\uff08%s\uff09\n\u9879\u76ee\uff1a%s\n\u5206\u7ec4\uff1a%s\nleader\uff1a%s\n\n"
             "\u5f00\u5de5\u5148\u8c03\u7528 /crew:%s \u8bfb\u5b88\u5219\u3002\n\n## \u4efb\u52a1\n\n%s" %
             (name, role, project_name, group if group is not None else "\u672a\u5206\u7ec4", leader_name, role, task))
    spec = {"id": spawn_id, "name": name, "group": group, "role": role, "task": task,
            "cwd": str(directory), "project": project_name, "agent": "crew:leader" if role == "leader" else None,
            "md": str(md_path), "created_at": store.now_iso() if now is None else now}
    return {"id": spawn_id, "session_id": session_id, "command": command(session_id, name, role), "spec": spec,
            "files": {str(json_path): json.dumps(spec, ensure_ascii=False, indent=2) + "\n", str(md_path): brief},
            "worktree_command": worktree_command}


def _execute(conn, plan, launcher, members=()):
    spec = plan["spec"]
    if plan["worktree_command"] is not None:
        result = subprocess.run(plan["worktree_command"], capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            raise board.BoardError("\u521b\u5efa worktree \u5931\u8d25: " + result.stderr.strip())
    if spec["role"] == "leader":
        board.ensure_spawn_group(conn, spec["project"], spec["name"], now=spec["created_at"])
    if members:
        board.roster(conn, spec["project"], "move", spec["name"], members, now=spec["created_at"])
    for path, content in plan["files"].items():
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
    launcher(plan["command"])
    board.record_event(conn, "spawn", spec["project"], board.HUMAN, {"id": plan["id"], "spec": spec, "command": plan["command"]}, spec["created_at"])
    return {"id": plan["id"], "command": plan["command"]}


def leader_plan(conn, home, plugin_root, project_name, name, members=(), task=None, cwd=None,
                new_worktree=None, base=None, now=None):
    plan = prepare(conn, home, plugin_root, project_name, name, "\u7ba1\u7406\u5206\u7ec4 " + name if task is None else task,
                   role="leader", cwd=cwd, new_worktree=new_worktree, base=base, now=now)
    for member in members:
        s = board.named_session(conn, project_name, _text(member, "\u7ec4\u5458\u540d\u5b57"))
        if s["role"] == "leader":
            raise board.BoardError("leader \u5fc5\u987b\u7559\u5728\u81ea\u5df1\u7684\u540c\u540d\u5206\u7ec4")
    return {**plan, "members": list(members)}


def request_start(home, project_name, path, body):
    address = Path(home) / "web.json"
    url = json.loads(address.read_text(encoding="utf-8"))["url"] if address.exists() else "http://127.0.0.1:47811"
    request = Request(url + path + "?" + urlencode({"project": project_name}),
                      json.dumps(body).encode("utf-8"), {"X-Crew": "1", "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        message = json.load(exc)["error"]
        raise (board.Conflict if exc.code == 409 else board.BoardError)(message) from exc
    except URLError as exc:
        raise board.BoardError("网页服务未运行，请先 crew web --open；" + str(exc.reason)) from exc


def cli_dispatch(args, cwd, plugin_root):
    if args.dry_run:
        conn = sqlite3.connect(store.db_path(args.home).resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None)
        conn.row_factory = sqlite3.Row
    else:
        conn = store.connect(args.home)
    with contextlib.closing(conn):
        project = board.project_for_path(conn, cwd)
        if project is None:
            raise board.BoardError("\u5f53\u524d\u76ee\u5f55\u4e0d\u5c5e\u4e8e\u5df2\u542f\u7528\u9879\u76ee\uff0c\u8bf7\u5148 crew init")
        if args.command == "leader":
            if not args.dry_run:
                body = {"name": args.name, "members": args.members.split(",") if args.members is not None else [],
                        "task": args.task, "cwd": str(args.cwd) if args.cwd is not None else None, "new_worktree": args.new_worktree}
                return request_start(args.home, project["name"], "/api/leader", body)
            return leader_plan(conn, args.home, plugin_root, project["name"], args.name,
                               args.members.split(",") if args.members is not None else (), args.task,
                               args.cwd, args.new_worktree)
        if not args.dry_run:
            return request_start(args.home, project["name"], "/api/spawn", {
                "name": args.name, "task": args.task, "group": args.group, "role": args.role,
                "cwd": str(args.cwd) if args.cwd is not None else None, "new_worktree": args.new_worktree, "base": args.base})
        plan = prepare(conn, args.home, plugin_root, project["name"], args.name, args.task, args.group,
                       args.role, args.cwd, args.new_worktree, args.base)
        return plan
