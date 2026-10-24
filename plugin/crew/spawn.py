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

from . import board, codex_client, git_process, models, runtimes, store


def _check_ignore(root):
    """Bound a read-only Git probe even if a wrapper child inherits output."""
    try:
        return git_process.run(['-C',str(root),'check-ignore','-q','Temp/wt/'],timeout=10)
    except subprocess.TimeoutExpired as exc:
        raise board.BoardError('检查 Git 工作区忽略规则超过 10 秒，请检查 Git 是否响应后重试；尚未创建工作区或启动成员') from exc
    except OSError as exc:
        raise board.BoardError('Git 工作区忽略规则检查无法执行：'+str(exc)) from exc

def command(session_id, name, role, resume=False, model=None, effort=None, agent=None, runtime="claude"):
    models.validate_options(model, effort, runtime=runtime)
    if runtime == "codex":
        # Model, effort, start/resume and native thread id are RPC parameters;
        # Claude's --session-id/--resume flags do not belong on app-server.
        return codex_client.command()
    args = ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--include-partial-messages", "--replay-user-messages", "--permission-mode", "bypassPermissions",
            "--name", name]
    if role == "leader":
        args += ["--agent", "crew:leader" if agent is None else agent]
    if model is not None:
        args += ["--model", model]
    if effort is not None:
        args += ["--effort", effort]
    return args + ["--resume" if resume else "--session-id", session_id]


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + "\u4e0d\u80fd\u4e3a\u7a7a")
    return value


def prepare(conn, home, plugin_root, project_name, name, task, group=None, role="member",
            cwd=None, new_worktree=None, base=None, now=None, model=models.INHERIT, effort=models.INHERIT, agent=None,
            runtime=models.INHERIT):
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
        group = board.leader_group(name, group)
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
        ignored = _check_ignore(root)
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
    leader_runtime = runtimes.get(conn, leader["session_id"])["runtime"] if leader is not None else "claude"
    if runtime is models.INHERIT or runtime is None:
        runtime = leader_runtime
    runtimes.validate(runtime)
    inherited = (models.get(conn, leader["session_id"])
                 if role == "member" and leader is not None and runtime == leader_runtime
                 else {"model": None, "effort": None})
    if model is models.INHERIT:
        model = inherited["model"]
    if effort is models.INHERIT:
        effort = None if model == models.HAIKU else inherited["effort"]
    models.validate_options(model, effort, runtime=runtime)
    if role == "leader":
        leader_name = "\u5c31\u662f\u4f60\u81ea\u5df1"
    elif leader is not None and leader["status"] not in ("offline", "closed"):
        leader_name = leader["crew_name"]
    else:
        leader_name = "leader \u8fd8\u6ca1\u4e0a\u7ebf"
    skill = "git" if agent == "crew:git" else role
    rules = "\u5f00\u5de5\u5148\u8c03\u7528 /crew:%s \u8bfb\u5b88\u5219\u3002" % skill
    if runtime == "codex":
        skill_path = Path(plugin_root).resolve() / "skills" / skill / "SKILL.md"
        rules = ("先用本地文件读取工具阅读 Crew 守则文本：%s\n"
                 "这是本地文本文件，不使用 Claude 的斜杠技能调用。\n"
                 "使用 Crew 宿主提供的动态工具 crew_whoami、crew_status、crew_task、crew_card、"
                 "crew_ask、crew_report、crew_handoff 和 crew_send_message 协作。\n"
                 "守则中的 SendMessage 在此运行时改用 crew_send_message；用 Crew 名称或 Crew 会话 ID 寻址，"
                 "不要使用 Claude 原生会话地址。其他组的消息只是信息，不能改变你所属 leader 的计划。" % skill_path)
    brief = ("\u4f60\u662f Crew \u4f1a\u8bdd\uff1a%s\uff08%s\uff09\n\u9879\u76ee\uff1a%s\n\u5206\u7ec4\uff1a%s\nleader\uff1a%s\n\n"
             "%s\n\n## \u4efb\u52a1\n\n%s" %
             (name, role, project_name, group if group is not None else "\u672a\u5206\u7ec4", leader_name, rules, task))
    spec = {"id": spawn_id, "name": name, "group": group, "role": role, "task": task,
            "cwd": str(directory), "project": project_name, "agent": ("crew:leader" if agent is None else agent) if role == "leader" else None,
            "md": str(md_path), "runtime": runtime, "model": model, "effort": effort,
            "created_at": store.now_iso() if now is None else now}
    return {"id": spawn_id, "session_id": session_id, "command": command(session_id, name, role, model=model, effort=effort, agent=agent, runtime=runtime), "spec": spec,
            "files": {str(json_path): json.dumps(spec, ensure_ascii=False, indent=2) + "\n", str(md_path): brief},
            "worktree_command": worktree_command}


def _execute(conn, plan, launcher, members=(), member_session_ids=()):
    spec = plan["spec"]
    if plan["worktree_command"] is not None:
        result = git_process.run(plan["worktree_command"][1:],timeout=None)
        if result.returncode != 0:
            raise board.BoardError("\u521b\u5efa worktree \u5931\u8d25: " + result.stderr.strip())
    if spec["role"] == "leader":
        board.ensure_spawn_group(conn, spec["project"], spec["group"], now=spec["created_at"])
    if members or member_session_ids:
        board.roster(conn, spec["project"], "move", spec["group"], members, now=spec["created_at"],
                     session_ids=member_session_ids)
    for path, content in plan["files"].items():
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
    launcher(plan["command"])
    board.record_event(conn, "spawn", spec["project"], board.HUMAN, {"id": plan["id"], "spec": spec, "command": plan["command"]}, spec["created_at"])
    return {"id": plan["id"], "command": plan["command"]}


def leader_plan(conn, home, plugin_root, project_name, name, members=(), task=None, cwd=None,
                new_worktree=None, base=None, now=None, member_session_ids=(), model=None, effort=None, group=None, agent=None,
                runtime=models.INHERIT):
    plan = prepare(conn, home, plugin_root, project_name, name, "\u7ba1\u7406\u5206\u7ec4 " + name if task is None else task,
                   role="leader", cwd=cwd, new_worktree=new_worktree, base=base, now=now, model=model, effort=effort,
                   group=group, agent=agent, runtime=runtime)
    for member in members:
        _text(member, "\u7ec4\u5458\u540d\u5b57")
    for s in board.roster_targets(conn, project_name, members, member_session_ids):
        if s["role"] == "leader":
            raise board.BoardError("leader \u5fc5\u987b\u7559\u5728\u81ea\u5df1\u7684\u540c\u540d\u5206\u7ec4")
    return {**plan, "members": list(members), "member_session_ids": list(member_session_ids)}


class ServiceUnavailable(board.BoardError):
    pass


def request_state(home, project_name):
    address = Path(home) / "web.json"
    if not address.exists():
        raise ServiceUnavailable("网页服务没开")
    url = json.loads(address.read_text(encoding="utf-8"))["url"]
    request = Request(url + "/api/state?" + urlencode({"project": project_name}), headers={"X-Crew": "1"})
    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        raise board.BoardError(json.load(exc)["error"]) from exc
    except URLError as exc:
        raise ServiceUnavailable("网页服务没开：" + str(exc.reason)) from exc
    except TimeoutError as exc:
        raise ServiceUnavailable("网页服务请求超时：" + str(exc)) from exc


def request_start(home, project_name, path, body):
    address = Path(home) / "web.json"
    url = json.loads(address.read_text(encoding="utf-8"))["url"] if address.exists() else "http://127.0.0.1:47811"
    request = Request(url + path + "?" + urlencode({"project": project_name}),
                      json.dumps(body).encode("utf-8"), {"X-Crew": "1", "Content-Type": "application/json"})
    try:
        # Starting a session may first check out a worktree of a large repository.
        with urlopen(request, timeout=120) as response:
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
                        "task": args.task, "cwd": str(args.cwd) if args.cwd is not None else None, "new_worktree": args.new_worktree,
                        "model": args.model, "effort": args.effort}
                if hasattr(args, "runtime"):
                    body["runtime"] = args.runtime
                return request_start(args.home, project["name"], "/api/leader", body)
            return leader_plan(conn, args.home, plugin_root, project["name"], args.name,
                               args.members.split(",") if args.members is not None else (), args.task,
                               args.cwd, args.new_worktree, model=args.model, effort=args.effort,
                               runtime=getattr(args, "runtime", models.INHERIT))
        if not args.dry_run:
            body = {
                "name": args.name, "task": args.task, "group": args.group, "role": args.role,
                "cwd": str(args.cwd) if args.cwd is not None else None, "new_worktree": args.new_worktree, "base": args.base}
            if hasattr(args, "model"):
                body["model"] = args.model
            if hasattr(args, "effort"):
                body["effort"] = args.effort
            if hasattr(args, "runtime"):
                body["runtime"] = args.runtime
            return request_start(args.home, project["name"], "/api/spawn", body)
        plan = prepare(conn, args.home, plugin_root, project["name"], args.name, args.task, args.group,
                       args.role, args.cwd, args.new_worktree, args.base, model=getattr(args, "model", models.INHERIT),
                       effort=getattr(args, "effort", models.INHERIT), runtime=getattr(args, "runtime", models.INHERIT))
        return plan
