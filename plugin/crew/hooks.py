"""Hook dispatch with explicit HOME and injectable environment/git discovery."""
import argparse
import contextlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from . import board, channel, git_process, goals, guard, install, messaging, rules, runtimes, store, trust_mode

EVENTS = ("session-start", "user-prompt-submit", "stop", "notification", "pre-ask", "post-ask", "session-end", "guard")


def _git(cwd, *args):
    result = git_process.run(["-C", str(cwd), *args], timeout=10)
    result.check_returncode()
    return result.stdout.strip()


def git_toplevel(cwd):
    return _git(cwd, "rev-parse", "--show-toplevel")


def git_details(cwd):
    if git_process.repository_root(cwd) is None:
        return None, None
    # A marker means Git must verify it. Broken repositories, missing Git and
    # timeouts must not silently register this checkout as a plain directory.
    result = git_process.run(["-C", str(cwd), "rev-parse", "--show-toplevel"], timeout=10)
    result.check_returncode()
    return store.norm_path(result.stdout.strip()), _git(cwd, "branch", "--show-current")


def _deliveries_text(deliveries):
    lines = ["待拍板 #%s 已答复：%s" % (d["id"], d["answer"]) for d in deliveries["answers"]]
    lines.extend("交接 #%s（来自 %s）：%s" % (h["id"], h["from_session"], h["body"]) for h in deliveries["handoffs"])
    return lines


def _spawn_task(home, spawn_id):
    try:
        spec = json.loads((Path(home).resolve() / "spawn" / (spawn_id + ".json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    lines = [line.strip() for line in str(spec.get("task") or "").splitlines() if line.strip()]
    return lines[0][:80] if lines else None


def _inactive_claude_runtime(home, session_id):
    """Read the host's current carrier choice without creating/migrating HOME."""
    if not store.db_path(home).exists():
        return False
    try:
        with contextlib.closing(guard.readonly(home)) as conn:
            registered = conn.execute('SELECT 1 FROM sessions WHERE session_id=?', (session_id,)).fetchone()
            return bool(registered and runtimes.get(conn, session_id)['runtime'] != 'claude')
    except (OSError, sqlite3.Error):
        # No positive inactive identity can be established. Preserve the
        # existing guard's explicit diagnostic/board-repair exceptions; normal
        # writes remain fail-closed in guard.check on an unreadable board.
        return False


def _goal_hook(event, data, home, plugin_root, environ, now):
    """Native goal UUIDs never become new people or consume the global inbox."""
    execution_id = environ.get('CREW_EXECUTION_ID')
    if not execution_id:
        return None
    with contextlib.closing(store.connect(home)) as conn:
        execution = goals.get_execution(conn, execution_id)
        native = goals.resolve_native(conn, 'claude', data['session_id'])
        if (native is None or native['id'] != execution_id or execution['runtime'] != 'claude'
                or execution['session_id'] != environ.get('CREW_SESSION_ID')):
            raise board.BoardError('目标执行与 Claude 原生会话不匹配')
        logical = {**data, 'session_id': execution['session_id']}
        if event == 'guard':
            gate = goals.dispatch_check(conn, execution['goal_id'], execution_id=execution_id)
            reason = None if gate['allowed'] else 'BLOCKED by Crew: ' + gate['reason']
            reason = reason or guard.check(home, logical, now=now)
            if reason:
                print(reason, file=sys.stderr)
                return 2, None
        # Process state is reconciled from the owning runtime, not a late hook.
        return 0, None


def handle(event, data, home, plugin_root, environ, git_provider=git_details, now=None):
    scoped = _goal_hook(event, data, home, plugin_root, environ, now)
    if scoped is not None:
        return scoped
    if _inactive_claude_runtime(home, data['session_id']):
        if event == 'guard':
            print('BLOCKED by Crew: 此 Crew 会话当前使用 Codex，旧 Claude 载体不能继续执行工具。' + guard.TAIL, file=sys.stderr)
            return 2, None
        # Late stop/session-end and prompt hooks from a superseded process may
        # neither overwrite the new carrier's status nor consume its mailbox.
        return 0, None
    if event == "guard":
        reason = guard.check(home, data, now=now)
        if reason is not None:
            print(reason, file=sys.stderr)
            return 2, None
        role = rules.SKILLS.get(data["tool_input"].get("skill")) if data.get("tool_name") == "Skill" else None
        if role is not None and guard.enabled_project(home, data["session_id"], data["cwd"]) is not None:
            with contextlib.closing(store.connect(home)) as conn:
                if conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (data["session_id"],)).fetchone() is not None:
                    rules.record_read(conn, data["session_id"], role, plugin_root, now=now)
        try:
            project_for_mail = guard.enabled_project(home, data["session_id"], data["cwd"])
        except (OSError, sqlite3.Error, ValueError):
            # guard.check already applied its explicit recovery exceptions.
            # Peer mail must not revoke permission to repair a broken board.
            project_for_mail = None
        if project_for_mail is not None:
            # Most tool calls have no peer mail. Keep that hot path read-only:
            # guard checks must not migrate the schema or obtain a write lock.
            with contextlib.closing(guard.readonly(home)) as conn:
                has_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='peer_messages'").fetchone()
                waiting = bool(has_table and messaging.pending(conn, data["session_id"]))
                row = conn.execute("SELECT status,crew_name FROM sessions WHERE session_id=?", (data["session_id"],)).fetchone()
                active = row is not None and row["crew_name"] and row["status"] != "closed" and not board.is_archived(conn, data["session_id"])
            if waiting and active:
                with contextlib.closing(store.connect(home)) as conn:
                    lines = messaging.take_for_hook(conn, data["session_id"], now=now)
                    if lines:
                        return 0, {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "\n\n".join(lines)}}
        return 0, None
    if event == "session-start":
        # Initialize HOME for every session, inside a project or not: the guard needs blacklist.txt everywhere.
        # An existing CLI source is never switched here; only explicit installs and desktop updates switch it.
        install.ensure(home, plugin_root, now=now)
    p = guard.enabled_project(home, data["session_id"], data["cwd"])
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
            if identity["spawn_id"]:
                identity["task"] = _spawn_task(home, identity["spawn_id"])
    with contextlib.closing(store.connect(home)) as conn:
        deliveries = board.apply_hook(conn, event, data, p["name"], identity=identity, git_info=git_info, now=now)
        lines = _deliveries_text(deliveries)
        if event in ("session-start", "user-prompt-submit"):
            session = board.session(conn, data["session_id"])
            if session["crew_name"] and session["status"] != "closed" and not board.is_archived(conn, data["session_id"]):
                lines.extend(messaging.take_for_hook(conn, data["session_id"], now=now))
                autonomy = trust_mode.context(conn, data["session_id"])
                if autonomy:
                    lines.append(autonomy)
        if event == "session-start":
            if board.is_archived(conn, data["session_id"]):
                control = board.session_control(conn, data["session_id"])
                lines = ["Crew 项目：%s" % p["name"], "本会话已%s，本次启动不会重新加入协作。" %
                         ("从 Crew 移除" if control["removed"] else "归档")]
            else:
                s = board.whoami(conn, data["session_id"])
                leader = s["leader"]
                lines = ["Crew 项目：%s" % p["name"], "我的 crew 名：%s" % (s["crew_name"] if s["crew_name"] is not None else "未收编（不要自己 crew join；用户说“收编到某某”时再照 /crew:member 收编）"),
                         "分组：%s；leader：%s；SendMessage 地址：%s" % (s["group_name"], leader["crew_name"] if leader else None, leader["cc_name"] if leader else None),
                         '命令行：python "%s" ...' % (Path(home).resolve() / "crew.py"),
                         "请遵守 /crew:%s" % rules.skill_role(conn, s)] + lines
                lines.append("跨执行工具的会话消息用 crew send <crew名> \"<内容>\"；crew who <名字> 可查 runtime。双方都是 Claude Code 时仍可用 SendMessage。")
            config = board.git_config(conn, p["name"])
            if config is not None:
                administrator = board.session(conn, config['admin_session_id']) if config['admin_session_id'] else None
                if config['admin_session_id'] == data['session_id']:
                    lines.append('你是本项目当前绑定的 Git 管理员，按项目规则统一推送、开 PR 和合并。')
                else:
                    lines.append("本项目由 git 管理员「%s」统一推送和合并。做完用 `crew git request` 交给它，不要自己 push、开 PR、合并。" %
                                 (administrator['crew_name'] if administrator else '未绑定'))
                lines.append("分支规则（全文用 crew git policy 看）：\n" + "\n".join(config["branch_rules"].splitlines()[:6])
                             if config["branch_rules"] else "git 管理员还没写分支规则，开工前先问它。全文用 crew git policy 看。")
        if event == "user-prompt-submit" or (event == "session-start" and data.get("source") in ("resume", "compact")):
            reminder = rules.reminder(conn, data["session_id"], plugin_root)
            if reminder is not None:
                lines.append(reminder)
        if event == "user-prompt-submit":
            summary = channel.summary(conn, data["session_id"], now=now)
            if summary is not None:
                lines.append(summary)
    if lines and event in ("session-start", "user-prompt-submit"):
        name = "SessionStart" if event == "session-start" else "UserPromptSubmit"
        return 0, {"hookSpecificOutput": {"hookEventName": name, "additionalContext": "\n".join(lines)}}
    return 0, None


def main(argv=None):
    parser = argparse.ArgumentParser(prog="crew_hook.py")
    parser.add_argument("--home", type=Path, default=Path(os.environ['CREW_HOME']) if os.environ.get('CREW_HOME') else store.default_home())
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
            if output is not None:
                print(json.dumps(output, ensure_ascii=False))
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
