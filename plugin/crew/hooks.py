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

from . import board, branch_claims, channel, domains, git_process, goals, guard, install, messaging, rules, runtimes, store

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


def managed(home, session_id):
    """Whether Crew runs this session now: it holds an unrevoked MCP token (design §7 step 3)."""
    if not store.db_path(home).exists():
        return False
    try:
        with contextlib.closing(guard.readonly(home)) as conn:
            return conn.execute('SELECT 1 FROM mcp_tokens WHERE session_id=? AND revoked_at IS NULL LIMIT 1',
                                (session_id,)).fetchone() is not None
    except sqlite3.Error:
        return False


def _goal_execution(home, native_id):
    """The goal execution a Claude native ID is bound to, read without creating or migrating HOME."""
    if not store.db_path(home).exists():
        return None
    try:
        with contextlib.closing(guard.readonly(home)) as conn:
            return goals.resolve_native(conn, 'claude', native_id)
    except sqlite3.Error:
        return None


def _goal_hook(event, data, home, plugin_root, now):
    """Native goal UUIDs never become new people or consume the global inbox."""
    execution = _goal_execution(home, data['session_id'])
    if execution is None:
        return None
    execution_id = execution['id']
    with contextlib.closing(store.connect(home)) as conn:
        if execution['runtime'] != 'claude':
            raise board.BoardError('目标执行与 Claude 原生会话不匹配')
        logical = {**data, 'session_id': execution['session_id']}
        if event == 'guard':
            gate = goals.dispatch_check(conn, execution['goal_id'], execution_id=execution_id, allow_discussion=True)
            reason = None if gate['allowed'] else 'BLOCKED by Crew: ' + gate['reason']
            reason = reason or guard.discussion_write_reason(conn, execution_id, logical.get('tool_name'))
            reason = reason or guard.check(home, logical, now=now, execution_id=execution_id)
            if not reason and data.get('tool_name') in guard.SHELLS:
                reason = branch_claims.task_branch_reason(home, data['cwd'], (data.get('tool_input') or {}).get('command', ''),
                                                          execution_id)
            if reason:
                print(reason, file=sys.stderr)
                return 2, None
    if event == 'guard' and data.get('tool_name') in guard.SHELLS:
        branch_claims.record_from_hook(home, execution['session_id'], data['cwd'],
                                       (data.get('tool_input') or {}).get('command', ''), execution_id=execution_id)
    # Process state is reconciled from the owning runtime, not a late hook.
    return 0, None


def handle(event, data, home, plugin_root, git_provider=git_details, now=None):
    scoped = _goal_hook(event, data, home, plugin_root, now)
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
        if data.get("tool_name") in guard.SHELLS:
            branch_claims.record_from_hook(home, data["session_id"], data["cwd"],
                                           (data.get("tool_input") or {}).get("command", ""))
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
                waiting = bool(has_table and messaging.has_pending_for_hook(conn, data["session_id"]))
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
    git_info = None
    with contextlib.closing(store.connect(home)) as conn:
        registered = conn.execute("SELECT spawn_id,task FROM sessions WHERE session_id=?", (data["session_id"],)).fetchone()
    if event == "session-start" or registered is None:
        git_info = git_provider(data["cwd"])
    # Crew registers the sessions it starts (name, role, group, spawn) before launching them.
    external = not managed(home, data["session_id"])
    with contextlib.closing(store.connect(home)) as conn:
        deliveries = board.apply_hook(conn, event, data, p["name"], git_info=git_info, now=now)
        if event == 'session-start' and registered is not None and registered['spawn_id'] and registered['task'] is None:
            task = _spawn_task(home, registered['spawn_id'])
            if task:
                conn.execute('UPDATE sessions SET task=? WHERE session_id=? AND task IS NULL', (task, data['session_id']))
                conn.commit()
        if event == 'user-prompt-submit' and external:
            guard.clear_general_handoff(conn, data['session_id'])
        lines = _deliveries_text(deliveries)
        if event in ("session-start", "user-prompt-submit"):
            session = board.session(conn, data["session_id"])
            if session["crew_name"] and session["status"] != "closed" and not board.is_archived(conn, data["session_id"]):
                lines.extend(messaging.take_for_hook(conn, data["session_id"], now=now))
                if external:
                    from . import trust_mode
                    _, authorization = trust_mode.input_context(conn, data['session_id'])
                    if authorization:
                        lines.append(authorization)
                    if event == 'session-start':
                        # Crew's own sessions get this with their inputs (chat_delivery), changes included.
                        placement = domains.placement(conn, data['session_id'])
                        if placement:
                            lines.append(placement)
        if event == "session-start":
            if board.is_archived(conn, data["session_id"]):
                control = board.session_control(conn, data["session_id"])
                lines = ["Crew 项目：%s" % p["name"], "本会话已%s，本次启动不会重新加入协作。" %
                         ("从 Crew 删除" if control["removed"] else "归档")]
            else:
                s = board.whoami(conn, data["session_id"])
                leader = s["leader"]
                lines = ["Crew 项目：%s" % p["name"], "我的 crew 名：%s" % (s["crew_name"] if s["crew_name"] is not None else "未收编（用户可在 Crew 的会话库里收编）"),
                         "分组：%s；leader：%s；SendMessage 地址：%s" % (s["group_name"], leader["crew_name"] if leader else None, leader["cc_name"] if leader else None),
                         "请遵守 Crew 注入的守则（[Crew {0}]，可能已被用户在设置里修改）。".format(rules.skill_role(conn, s))] + lines
                if not external:
                    lines.append("与同事协作用 crew MCP 工具（message_send、channel_post 等，其余用 crew_search 查找）；"
                                 "双方都是 Claude Code 时仍可用 SendMessage。")
            config = board.git_config(conn, p["name"])
            if config is not None:
                administrator = board.session(conn, config['admin_session_id']) if config['admin_session_id'] else None
                if config['admin_session_id'] == data['session_id']:
                    lines.append('你是本项目当前绑定的 Git 助手，处理项目规则授权的推送、PR 与合并；发版分支由用户自己处理，本地集成分支不推送。')
                else:
                    lines.append("本项目的推送、开 PR、合并交给 Git 助手「%s」：做完用 Crew 工具 git_request_add 交给它；"
                                 "自己不 push、不开 PR、不合并。发版分支由用户自己处理。" %
                                 (administrator['crew_name'] if administrator else '未绑定'))
                lines.append("分支规则（全文用 git_policy_get 看）：\n" + "\n".join(config["branch_rules"].splitlines()[:6])
                             if config["branch_rules"] else "Git 助手还没写分支规则，开工前先问它。全文用 git_policy_get 看。")
        if event == "user-prompt-submit":
            # Crew writes notices into the input of sessions it runs; an external one only hears them here.
            summary = channel.summary(conn, data["session_id"], now=now, carry_pending=external)
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
            code, output = handle(args.event, data, args.home, Path(__file__).resolve().parents[1])
            if output is not None:
                print(json.dumps(output, ensure_ascii=False))
            return code
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, re.error) as exc:
            print("%s: %s. %s" % (type(exc).__name__, str(exc).replace("\n", " "), guard.TAIL), file=sys.stderr)
            return 2
    try:
        data = json.loads(sys.stdin.read())
        code, output = handle(args.event, data, args.home, Path(__file__).resolve().parents[1])
        if output is not None:
            print(json.dumps(output, ensure_ascii=False))
        return code
    except Exception as exc:
        # DESIGN section 5 explicitly requires attendance errors to be isolated here.
        print("%s: %s" % (type(exc).__name__, str(exc).replace("\n", " ")), file=sys.stderr)
        return 1
