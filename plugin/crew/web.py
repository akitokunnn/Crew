"""Loopback-only board HTTP service with injectable external sources."""
import csv
import datetime as dt
import io
import json
import mimetypes
import os
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import ProxyHandler, build_opener
from urllib.error import HTTPError, URLError

from . import board, channel, channel_digest, chat, claude_limits, codex_runtime, documents, git_admin, git_ci, git_view, goal_api, goals, hooks, maintenance, messaging, models, notifications, open_session, presentation, runtime_catalog, runtime_switch, runtimes, session_library, session_ops, settings, spawn, store, themes, todo, trust_mode, updater, usage, usage_cli, vscode_sessions, workspace_execution, workspace_settings
from .service_lock import HomeServiceLock
from . import member_defaults, operations_chat, operations_settings
from . import connections, session_presence, storage_cleanup, workspace_activity


def _text(data, key):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("\u5b57\u6bb5\u5fc5\u987b\u662f\u975e\u7a7a\u6587\u672c: " + key)
    return value


def _optional_text(data, key):
    return None if data.get(key) is None else _text(data, key)


# HTTP entries that start or feed model work must pass the update gate before
# any side effect (adoption, git admin setup, VS Code takeover, digest).
MODEL_WORK = {"/api/spawn", "/api/leader", "/api/wake", "/api/chat/send", "/api/session/compact",
              "/api/open", "/api/open/continue", "/api/git/enable", "/api/channel/digest",
              "/api/maintenance/department/compact"}


class Service:
    def __init__(self, home, plugin_root, agents_provider=None,
                 claude_root=None, codex_root=None, runs_file=None, now=None, cwd=None, process_factory=subprocess.Popen,
                 notification_client=None, lock_status=notifications.desktop_locked, notification_batch_seconds=30,
                 vscode_state_db=None, migrate_manual_work=False):
        if agents_provider is None:
            from .cli import claude_agents
            agents_provider = claude_agents
        self.home, self.plugin_root = Path(home), Path(plugin_root)
        self.agents_provider, self.clock = agents_provider, now
        # Health reports which bundled source this process really runs.
        self.instance_id = uuid.uuid4().hex
        self.version = updater.manifest_version(self.plugin_root)
        self.update_drain_seconds = 10
        self.chat = chat.Manager(home, plugin_root, agents_provider, process_factory)
        self.runtime_catalog = runtime_catalog.Catalog()
        self.migrate_manual_work = migrate_manual_work
        self.claude_root = Path.home() / ".claude" / "projects" if claude_root is None else Path(claude_root)
        self.codex_root = Path.home() / ".codex" / "sessions" if codex_root is None else Path(codex_root)
        self.runs_file = self.home / "codex_runs.jsonl" if runs_file is None else Path(runs_file)
        self.agents_lock, self.scan_lock, self.write_lock = threading.Lock(), threading.Lock(), threading.Lock()
        self.channel_digest = channel_digest.Manager(home, self.write_lock, process_factory)
        self.agents, self.agents_at = [], None
        self.agents_error = "\u6b63\u5728\u83b7\u53d6\u8fdb\u7a0b\u72b6\u6001"
        self.scanned_at = None
        self.crew_usage = (None, None)
        self.title_cache = {}
        self.vscode_state_db = (Path(os.environ["APPDATA"]) / "Code" / "User" / "globalStorage" / "state.vscdb"
                                if vscode_state_db is None else Path(vscode_state_db))
        self.vscode_session_caches = {}
        self.vscode_sessions, self.vscode_record_ids = {}, {}
        self.vscode_hidden_ids = None
        self.vscode_error = "还没读到 VS Code 会话列表"
        self.stop = threading.Event()
        self.agents_ready = threading.Event()
        self.worker = None
        self.notice_worker = None
        self.git_ci = git_ci.Poller(self.home, self.write_lock)
        self.ci_worker = None
        self.connections = connections.Manager(self, notification_client, lock_status, notification_batch_seconds)
        self.inbox = self.connections.inboxes['qq']
        # Marks maintenance left unfinished by a previous service as interrupted; nothing is re-sent.
        self.maintenance = maintenance.Manager(self)
        self.storage_cleanup = storage_cleanup.Manager(self.home, self.plugin_root, clock=self.now,
                                                      active_keys=self.storage_active_keys, activity_lock=self.chat.lock)
        with closing(store.connect(home)) as conn:
            projects = board.projects(conn)
            project = board.project_for_path(conn, Path.cwd() if cwd is None else cwd)
            self.default_project = project["name"] if project is not None else None

    def now(self):
        if self.clock is None:
            return dt.datetime.now(dt.timezone.utc)
        return self.clock() if callable(self.clock) else self.clock

    def storage_active_keys(self):
        # A cached process can still be draining its last journal writes after
        # exit. Keep its attachment directory protected for this service life.
        with self.chat.lock:
            return frozenset(self.chat.processes)

    def refresh_agents(self):
        try:
            agents = self.agents_provider()
            if not isinstance(agents, list):
                raise ValueError("claude agents \u5fc5\u987b\u8fd4\u56de\u6570\u7ec4")
            for agent in agents:
                if not isinstance(agent["sessionId"], str) or not isinstance(agent["name"], str):
                    raise ValueError("claude agents \u8eab\u4efd\u975e\u6cd5")
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
            self.chat.goal_controller.live_native = None
            with self.agents_lock:
                self.agents_error = "\u8fdb\u7a0b\u72b6\u6001\u83b7\u53d6\u5931\u8d25\uff1a%s: %s" % (type(exc).__name__, exc)
        else:
            self.chat.goal_controller.live_native = frozenset(agent['sessionId'] for agent in agents)
            with self.agents_lock:
                self.agents, self.agents_at, self.agents_error = agents, self.now(), None
        self.agents_ready.set()

    def _poll_agents(self):
        while not self.stop.is_set():
            self.refresh_agents()
            self._refresh_vscode()
            try:
                self.digest_channels()
                with self.agents_lock:
                    agents, error = self.agents, self.agents_error
                with self.write_lock, closing(store.connect(self.home)) as conn:
                    self.chat.auto_resume(conn, agents, now=self.now().isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                                          runtime=None if error is None else 'codex')
            except (sqlite3.Error, OSError, subprocess.SubprocessError):
                # A temporary failure in another task sharing this worker must
                # not permanently disable agent discovery and VS Code scans.
                traceback.print_exc()
            if self.stop.wait(10):
                break

    def digest_channels(self):
        with self.chat.work(background=True) as admitted:
            if not admitted:
                return
            with self.write_lock, closing(store.connect(self.home)) as conn:
                for project in board.projects(conn):
                    self.channel_digest.start(conn, project['name'], trigger='auto')

    def _refresh_vscode(self):
        try:
            self._read_vscode()
        except (sqlite3.Error, OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
            # Transcripts and worktrees can change during a read. A source
            # failure must not terminate the shared agents/auto-resume worker.
            # Keep the last good snapshot and retry on the next poll.
            with self.agents_lock:
                self.vscode_error = "VS Code 会话扫描失败（10 秒后重试）：%s: %s" % (type(exc).__name__, exc)

    def _read_vscode(self):
        with self.write_lock, self.scan_lock, closing(store.connect(self.home)) as conn:
            projects = board.projects(conn)
        sessions, record_ids, errors = {}, {}, []
        with self.agents_lock:
            previous_sessions = self.vscode_sessions.copy()
            previous_ids = self.vscode_record_ids.copy()
        for project in projects:
            name = project["name"]
            try:
                roots = vscode_sessions.project_roots(project["root"])
                cache = self.vscode_session_caches.setdefault(name, vscode_sessions.SessionList())
                sessions[name] = cache.list(self.claude_root, roots)
                record_ids[name] = cache.record_ids.copy()
                errors.extend(name + ": " + error for error in cache.errors)
            except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
                sessions[name] = previous_sessions.get(name, [])
                record_ids[name] = previous_ids.get(name, set())
                errors.append("%s: %s: %s" % (name, type(exc).__name__, exc))
        source_error = "VS Code 来源读取失败（10 秒后重试）：" + "; ".join(errors[:10]) if errors else None
        try:
            hidden = vscode_sessions.hidden_ids(self.vscode_state_db)
        except (sqlite3.Error, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            with self.agents_lock:
                self.vscode_sessions, self.vscode_record_ids = sessions, record_ids
                reason = "%s: %s" % (type(exc).__name__, exc)
                archive_error = ("还没读到 VS Code 会话列表；" + reason
                                 if self.vscode_hidden_ids is None else reason)
                self.vscode_error = archive_error + ("；" + source_error if source_error else "")
        else:
            with self.agents_lock:
                self.vscode_sessions, self.vscode_record_ids = sessions, record_ids
                self.vscode_hidden_ids, self.vscode_error = hidden, source_error
            with self.write_lock, self.scan_lock, closing(store.connect(self.home)) as conn:
                now = self.now().isoformat(timespec="milliseconds").replace("+00:00", "Z")
                for session in conn.execute("SELECT session_id,project FROM sessions WHERE crew_name IS NOT NULL AND crew_name<>''"):
                    sid = session["session_id"]
                    if sid in hidden and not board.is_archived(conn, sid):
                        self.archive_session(conn, sid, now=now)
                        board.record_event(conn, "vscode:archived", session["project"], sid, {}, now=now)

    def start(self):
        if self.migrate_manual_work:
            with closing(store.connect(self.home)) as conn:
                todo.backfill(conn)
        self.worker = threading.Thread(target=self._poll_agents, name="crew-agents", daemon=True)
        self.worker.start()
        self.notice_worker = threading.Thread(target=self._poll_notices, name="crew-leader-notices", daemon=True)
        self.notice_worker.start()
        self.ci_worker = threading.Thread(target=self._poll_git_ci, name="crew-git-ci", daemon=True)
        self.ci_worker.start()
        self.connections.start()
        self.storage_cleanup.start()

    def close(self):
        self.stop.set()
        # Cancel independent digest natives before joining other pollers.
        actions = [self.channel_digest.stop, self.storage_cleanup.close, self.connections.close]
        if self.notice_worker is not None:
            actions.append(self.notice_worker.join)
        if self.ci_worker is not None:
            actions.append(self.ci_worker.join)
        actions.extend([self.chat.close, self.maintenance.close])
        if self.worker is not None:
            actions.append(lambda: self.worker.join(timeout=1))
        actions.append(self.channel_digest.close)
        errors = []
        for close in actions:
            try:
                close()
            except Exception as exc:
                # Preserve every failure while releasing the remaining owned
                # resources. The owner still receives the original exception.
                traceback.print_exc()
                errors.append(exc)
        if errors:
            raise errors[0]

    def _poll_notices(self):
        while not self.stop.wait(2):
            self.release_abandoned_update()
            with self.chat.work(background=True) as admitted:
                if not admitted:
                    continue
                try:
                    with self.chat.lock, closing(store.connect(self.home)) as conn:
                        self.chat.goal_controller.sync(conn)
                        self.chat.goal_controller.resume_operations(conn)
                    self.chat.deliver_messages()
                    self.deliver_notices()
                except (board.BoardError, sqlite3.Error, OSError):
                    traceback.print_exc()

    def release_abandoned_update(self):
        # The gate lives only while an updater holds the HOME update lock.
        gate = self.chat.paused()
        if gate is not None and not updater.update_lock_held(self.home) and self.chat.open_admission(gate):
            print("Crew 更新器已退出，已解除更新闸", file=sys.stderr, flush=True)

    def update_reasons(self, conn):
        """Whole-HOME idleness from real process/worker state, not sessions.status."""
        reasons = self.chat.busy_reasons(conn)
        try:
            live = {agent["sessionId"] for agent in self.agents_provider()}
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
            reasons.append("无法确认 Claude 进程状态：%s: %s" % (type(exc).__name__, exc))
            live = None
        for row in conn.execute("SELECT session_id,crew_name FROM sessions WHERE crew_name IS NOT NULL "
                                "AND crew_name<>'' AND status!='closed'").fetchall():
            sid, name = row["session_id"], row["crew_name"]
            if board.is_archived(conn, sid) or self.chat.running(sid):
                continue
            if live is not None and sid in live:
                reasons.append("%s 在 VS Code 或终端中运行，请先结束该会话" % name)
            elif runtimes.get(conn, sid)["runtime"] == "codex":
                try:
                    orphan = codex_runtime.lease_alive(self.home, sid)
                except (OSError, ValueError) as exc:
                    reasons.append("无法确认 %s 的 Codex 进程状态：%s" % (name, exc))
                    continue
                if orphan:
                    reasons.append("%s 的 Codex 进程仍在运行" % name)
        # Goal executions keep native identities separately from person sessions.
        for row in conn.execute("SELECT e.id,e.runtime,b.native_id FROM goal_executions e LEFT JOIN "
                                "goal_execution_bindings b ON b.execution_id=e.id AND b.runtime=e.runtime"):
            if self.chat.running(row["id"]):
                continue
            if row["runtime"] == "claude" and live is not None and row["native_id"] in live:
                reasons.append("目标执行 %s 的 Claude 会话仍在外部运行" % row["id"])
            elif row["runtime"] == "codex":
                try:
                    orphan = codex_runtime.lease_alive(self.home, row["id"])
                except (OSError, ValueError) as exc:
                    reasons.append("无法确认目标执行 %s 的 Codex 进程状态：%s" % (row["id"], exc))
                    continue
                if orphan:
                    reasons.append("目标执行 %s 的 Codex 进程仍在运行" % row["id"])
        from .operations import read_operations
        if read_operations(self.home)["active_worker"] is not None:
            reasons.append("系统管理员的修复工作仍在运行，等待其完成后更新")
        with self.channel_digest.lock:
            reasons.extend("交流区摘要正在生成：" + project for project in sorted(self.channel_digest.running))
        return reasons

    def prepare_update(self, data):
        if data.get("instance_id") != self.instance_id:
            raise board.Conflict("后台实例已变化，请重新检查后再更新")
        if not updater.update_lock_held(self.home):
            raise board.Conflict("没有持有更新锁的 Crew 更新器，不能暂停新任务")
        gate = uuid.uuid4().hex
        if not self.chat.close_admission(gate, self.update_drain_seconds):
            return {"ok": False, "status": "busy", "reasons": ["有新任务正在启动，稍后重试"]}
        reasons = None
        try:
            with closing(store.connect(self.home)) as conn:
                reasons = self.update_reasons(conn)
        finally:
            if reasons != []:
                self.chat.open_admission(gate)
        if reasons:
            return {"ok": False, "status": "busy", "reasons": reasons}
        return {"ok": True, "status": "ready", "gate_id": gate, "instance_id": self.instance_id,
                "pid": os.getpid(), "managed_pids": self.chat.managed_pids()}

    def cancel_update(self, data):
        if data.get("instance_id") != self.instance_id:
            raise board.Conflict("后台实例已变化，更新准备已失效")
        if not self.chat.open_admission(_text(data, "gate_id")):
            raise board.Conflict("更新准备已失效")
        return {"ok": True}

    def connection_plan(self):
        sessions = []
        with closing(store.connect(self.home)) as conn:
            projects = board.projects(conn)
            goal_count = conn.execute("SELECT COUNT(*) FROM goals WHERE status NOT IN (?,?,?)", tuple(goals.TERMINAL)).fetchone()[0]
        for project in projects:
            for session in self.workspace_state(project['name'])['sessions']:
                sessions.append({key: session[key] for key in ('session_id', 'name', 'running', 'busy')}
                                | {'project': project['display_name'] or project['name']})
        from .operations import read_operations
        return {'sessions': sessions, 'goals_count': goal_count,
                'running_count': sum(item['running'] for item in sessions),
                'repair_active': read_operations(self.home)['active_worker'] is not None,
                'operations_chat_busy': operations_chat.snapshot(self.home)['busy']}

    def prepare_connection(self, data):
        if data.get('instance_id') != self.instance_id:
            raise board.Conflict('后台实例已变化，请重新检查连接设置')
        if not updater.update_lock_held(self.home):
            raise board.Conflict('未取得后台切换锁，不能停止会话')
        plan = self.connection_plan()
        if plan['repair_active']:
            raise board.Conflict('运维修复仍在运行，请等待本次修复结束后切换')
        gate = uuid.uuid4().hex
        if not self.chat.close_admission(gate, self.update_drain_seconds):
            raise board.Conflict('有新任务正在启动，请稍后重试')
        try:
            operations_chat.interrupt(self.home)
            deadline = time.monotonic() + 15
            while operations_chat.snapshot(self.home)['busy']:
                if time.monotonic() >= deadline:
                    raise board.Conflict('运维 AI 尚未停止，后台目录未切换')
                self.stop.wait(.1)
            with self.write_lock, self.chat.lock, closing(store.connect(self.home)) as conn:
                managed_pids = self.chat.managed_pids()
                for project in board.projects(conn):
                    workspace_execution.stop_project(self.chat, conn, project['name'])
                reasons = self.update_reasons(conn)
            if reasons:
                raise board.Conflict('；'.join(reasons))
            return {'ok': True, 'status': 'ready', 'gate_id': gate, 'instance_id': self.instance_id,
                    'pid': os.getpid(), 'managed_pids': managed_pids, 'sessions': plan['sessions']}
        except Exception:
            self.chat.open_admission(gate)
            raise

    def _poll_git_ci(self):
        while not self.stop.is_set():
            self.git_ci.poll(now=self.now().isoformat())
            if self.stop.wait(300):
                break

    def deliver_notices(self):
        with self.write_lock, closing(store.connect(self.home)) as conn:
            self.deliver_git_results(conn)
            leaders = [dict(row) for row in conn.execute(
                "SELECT DISTINCT s.* FROM sessions s JOIN leader_notices n ON n.session_id=s.session_id "
                "WHERE n.sent_at IS NULL AND s.role='leader' AND s.status!='closed'")]
            for leader in leaders:
                sid = leader['session_id']
                try:
                    with self.chat.lock:
                        execution = self.chat.goal_controller.routing_execution(conn, sid)
                        key = execution['id'] if execution else sid
                        if messaging.stopped(conn, sid) or execution and not goals.dispatch_check(
                                conn, execution['goal_id'], execution_id=key)['allowed']:
                            continue
                        if self.chat.running(key):
                            if self.chat.busy(key):
                                continue
                        else:
                            with self.agents_lock:
                                agents, agents_error = self.agents, self.agents_error
                            # Unknown process state could mean the leader runs elsewhere; resuming then would start a second copy.
                            claude = runtimes.get(conn, sid)['runtime'] == 'claude'
                            if claude and (agents_error is not None or not self.agents_ready.is_set()):
                                continue
                            if claude and any(agent['sessionId'] == sid for agent in agents):
                                if conn.execute("SELECT 1 FROM events WHERE session_id=? AND kind='leader:notice_blocked'",
                                                (sid,)).fetchone() is None:
                                    board.record_event(conn, 'leader:notice_blocked', leader['project'], sid,
                                                       {'reason': '会话正在其他进程中运行'}, now=self.now().isoformat())
                                continue
                            if not self.chat.path(key).exists():
                                continue
                        pending = board.pending_notices(conn, sid)
                        text = '[Crew]\n' + '\n'.join('%s. %s' % (index, notice['text'])
                                                       for index, notice in enumerate(pending, 1))
                        self.chat.send(leader, text, source='crew')
                        for notice in pending:
                            board.mark_notice_sent(conn, notice['id'], now=self.now().isoformat())
                except (board.BoardError, OSError) as exc:
                    board.record_event(conn, 'leader:notice_error', leader['project'], sid,
                                       {'error': '%s: %s' % (type(exc).__name__, exc)}, now=self.now().isoformat())

    def deliver_git_results(self, conn):
        pending = conn.execute(
            "SELECT q.*,h.body FROM git_result_handoffs q JOIN handoffs h ON h.id=q.handoff_id "
            "JOIN git_merge_requests r ON r.id=q.request_id AND r.project=h.project AND r.session_id=q.session_id "
            "WHERE (r.status='rejected' OR (r.status='merged' AND r.blocking=1)) AND NOT EXISTS(SELECT 1 FROM handoff_deliveries d "
            "WHERE d.handoff_id=q.handoff_id AND d.session_id=q.session_id) ORDER BY q.handoff_id").fetchall()
        for result in pending:
            sid = result['session_id']
            target = board.session(conn, sid)
            if target['status'] == 'closed' or board.is_archived(conn, sid):
                continue
            try:
                with self.chat.lock:
                    execution = self.chat.goal_controller.routing_execution(conn, sid)
                    key = execution['id'] if execution else sid
                    if messaging.stopped(conn, sid) or execution and not goals.dispatch_check(
                            conn, execution['goal_id'], execution_id=key)['allowed']:
                        continue
                    with self.agents_lock:
                        claude = runtimes.get(conn, sid)['runtime'] == 'claude'
                        if claude and not self.chat.running(key) and (self.agents_error is not None or not self.agents_ready.is_set()):
                            continue
                        external = claude and any(agent['sessionId'] == sid for agent in self.agents)
                    if self.chat.running(key):
                        if self.chat.busy(key):
                            continue
                        self.chat.send(target, '[Crew]\n' + result['body'], source='crew')
                    else:
                        # Unadopted requesters cannot be woken; their next prompt picks the result up as a handoff.
                        if external or target['crew_name'] is None:
                            continue
                        self.chat.wake(conn, target, result['body'],
                                       caller_session_id=result['admin_session_id'], now=self.now().isoformat())
                    conn.execute("INSERT OR IGNORE INTO handoff_deliveries VALUES(?,?,?)",
                                 (result['handoff_id'], sid, self.now().isoformat()))
            except (board.BoardError, OSError) as exc:
                board.record_event(conn, 'git:result_error', target['project'], sid,
                                   {'handoff_id': result['handoff_id'], 'error': '%s: %s' % (type(exc).__name__, exc)},
                                   now=self.now().isoformat())

    def selected_project(self, conn, name):
        name = self.default_project if name is None else name
        if name is None:
            raise board.BoardError("\u8bf7\u5148 crew init \u542f\u7528\u9879\u76ee")
        return board.project(conn, name)["name"]

    def candidates(self, conn, project_name):
        with self.agents_lock:
            agents, error = self.agents, self.agents_error
            snapshot = self.vscode_sessions.get(project_name, [])
            record_ids = self.vscode_record_ids.get(project_name, set())
            hidden = self.vscode_hidden_ids
        live = board.live_sessions(agents) if error is None else {}
        project_root = board.project(conn, project_name)["root"]
        registered = {row["session_id"]: row for row in conn.execute("SELECT * FROM sessions")}
        sources = {s["session_id"]: s for s in snapshot if hidden is not None or s["session_id"] in live}
        scoped_native = self.chat.goal_controller.native_ids(conn)
        sources = {sid: value for sid, value in sources.items() if sid not in scoped_native}
        for sid, agent in live.items():
            if sid in scoped_native or sid in sources or sid in record_ids or not agent.get("cwd"):
                continue
            project = board.project_for_path(conn, agent["cwd"])
            if project is not None and project["name"] == project_name:
                sources[sid] = {"session_id": sid, "title": agent["name"], "cwd": agent["cwd"],
                                "mtime": agent["startedAt"] / 1000 if agent.get("startedAt") is not None else None}
        result = {"running": [], "others": []}
        for sid, source in sources.items():
            if hidden is not None and sid in hidden:
                continue
            project = board.project_for_path(conn, source["cwd"])
            if project is None or project["name"] != project_name:
                continue
            session = registered.get(sid)
            if session is not None and (session["project"] != project_name or session["role"] == "leader" or session["status"] == "closed"
                                        or session["group_name"] is not None or board.is_archived(conn, sid)):
                continue
            running = sid in live
            crew_name = session["crew_name"] if session is not None else None
            candidate = {"session_id": sid, "title": source["title"], "cwd": source["cwd"],
                         "rel_dir": os.path.relpath(source["cwd"], project_root).replace("\\", "/"),
                         "last_active": (dt.datetime.fromtimestamp(source["mtime"], dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                                         if source["mtime"] is not None else None),
                         "running": running, "status": ("working" if live[sid]["status"] == "busy" else "idle") if running else "offline",
                         "crew_name": crew_name, "group": session["group_name"] if session is not None else None}
            result["running" if running else "others"].append(candidate)
        result["others"].sort(key=lambda candidate: (candidate["last_active"], candidate["session_id"]), reverse=True)
        return result

    def candidate(self, conn, project_name, session_id):
        candidate = next((s for bucket in self.candidates(conn, project_name).values() for s in bucket
                          if s["session_id"] == session_id), None)
        if candidate is None:
            raise board.BoardError("会话不在当前项目的待收编列表: " + session_id)
        return candidate

    def adopt(self, conn, project_name, candidate, name, group_name, now):
        with board.transaction(conn):
            return self._adopt(conn, project_name, candidate, name, group_name, now)

    def _adopt(self, conn, project_name, candidate, name, group_name, now):
        sid = candidate["session_id"]
        if candidate["crew_name"]:
            if name.strip() != candidate["crew_name"]:
                board.available_name(conn, project_name, name.strip(), session_id=sid)
            if group_name is not None:
                board.roster.__wrapped__(conn, project_name, "add", group_name, session_ids=[sid], now=now)
            if name.strip() != candidate["crew_name"]:
                session_ops.rename.__wrapped__(conn, sid, name, now=now)
        else:
            worktree, branch = hooks.git_details(candidate["cwd"])
            board.register_session.__wrapped__(conn, sid, project_name, candidate["cwd"], worktree, branch,
                                   name=name, group_name=group_name, now=now)
        with self.agents_lock:
            live = board.live_sessions(self.agents) if self.agents_error is None else {}
        cc_name = live[sid]["name"] if sid in live else None
        session = board.update_cc_name.__wrapped__(conn, sid, cc_name, now=now)
        leader = board.leader_of(conn, sid)
        if leader is not None:
            peer = any(runtimes.get(conn, s['session_id'])['runtime'] == 'codex' for s in (session, leader))
            address = "（SendMessage 地址：%s）" % session["cc_name"] if session["cc_name"] else ""
            notice = ("%s%s 已收编到你组。请用 SendMessage 欢迎它、说明你是它的 leader、守则 /crew:member，"
                "并请它用 crew card 写下负责范围和完成标准；crew who 显示离线时，用 crew wake %s \"要它做的事\" "
                "叫醒后按 crew 名 SendMessage。" % (session["crew_name"], address, session["crew_name"]))
            if peer:
                notice = ('%s 已收编到你组。请用 crew send %s "<欢迎与分工>"，说明你是它的 leader，'
                          '请它阅读成员守则并用 crew card 写下负责范围和完成标准。离线时用 crew wake 接续原会话。' %
                          (session['crew_name'], session['crew_name']))
            board.queue_leader_notice.__wrapped__(conn, leader["session_id"],
                notice,
                "crew", now=now)
        return session

    def state(self, project_name=None):
        self.scan_usage()
        with self.agents_lock:
            agents, error = self.agents, self.agents_error
        with closing(store.connect(self.home)) as conn:
            result = {'project': None, 'projects': [{key: p[key] for key in ('name', 'display_name', 'theme')} for p in board.projects(conn)],
                      'themes': themes.THEMES,
                      'models': models.MODELS, 'efforts': models.EFFORTS,
                      'runtimes': self.runtime_catalog.snapshot(),
                      'ai': settings.load(conn),
                      'now': self.now().isoformat(), 'agents_error': error, 'notification': self.notification_status(),
                      'leaders': [], 'members': [], 'decisions': [], 'attention': [],
                      'todos': [], 'todo_count': 0, 'goals': [], 'native_approvals': [],
                      'candidates': {'running': [], 'others': []},
                      'channel': {'unread': 0, 'latest_id': 0, 'digesting': False, 'digest_error': None, 'digest_id': None},
                      'limits': self.limits(conn)}
            result['vscode_error'] = self.vscode_error
            if project_name is not None:
                name = self.selected_project(conn, project_name)
                result['goals'] = [goal_api.card(conn, goal) for goal in goals.list_goals(conn, name)]
                card = channel.digest(conn, name)
                result['channel'] = {**channel.state(conn, name), **self.channel_digest.state(name),
                                     'digest_id': card['id'] if card is not None else None}
                status, present, agents = session_presence.project_status(self, conn, name, agents, error)
                git_config = board.git_config(conn, name)
                result['project'] = {key: status['project'][key] for key in ('name', 'display_name', 'root', 'theme')}
                result['todos'] = todo.list(conn, name)
                links = {(row['kind'], row['item_id']): row['goal_id'] for row in conn.execute(
                    'SELECT l.kind,l.item_id,l.goal_id FROM goal_links l JOIN goals g ON g.id=l.goal_id WHERE g.project=?', (name,))}
                for item in result['todos']:
                    item['goal_id'] = links.get(('todo', item['id']))
                result['todo_count'] = len(result['todos'])
                open_groups = {group['name'] for group in status['groups'] if group['closed_at'] is None}
                removed = {r[0] for r in conn.execute('SELECT session_id FROM session_controls WHERE removed=1')}
                sessions = [s for s in [*status['ungrouped'], *(s for group in status['groups'] for s in group['members'])]
                            if s['session_id'] not in removed and s['status'] != 'closed']
                member_choices = member_defaults.for_project(conn, name)
                with self.scan_lock:
                    titles = usage.session_titles(self.claude_root, [s['session_id'] for s in sessions], self.title_cache)
                    contexts = {sid: cached['context'] for sid, cached in self.title_cache.items()}
                result['native_approvals'] = self.native_approvals(name)
                histories, process_keys, active_goals = {}, {}, {}
                for session in sessions:
                    sid = session['session_id']
                    execution = self.chat.goal_controller.routing_execution(conn, sid)
                    key = process_keys[sid] = execution['id'] if execution else sid
                    active_goals[sid] = execution['goal_id'] if execution else None
                    session['web_chat'] = self.chat.path(sid).exists()
                    if session['web_chat']:
                        histories[sid] = self.chat.history(sid)
                    session['status'] = session_presence.status(self.chat, session, present, key)
                decisions = board.decisions(conn, name, all=True)
                for decision in decisions:
                    if decision['status'] == 'open':
                        asker = board.session(conn, decision['session_id'])
                        leader_id=trust_mode.decision_leader(conn,decision['id'])
                        result['decisions'].append({**{key: decision[key] for key in (
                            'id', 'session_id', 'question', 'options', 'recommendation', 'blocking', 'created_at', 'held', 'held_at')},
                            'asker': asker['crew_name'], 'leader_session_id': leader_id,
                            'goal_id': links.get(('decision', decision['id']))})
                tasks = usage.codex_tasks(conn, None, None, project=name)
                subagent_keys = {}
                for row in conn.execute('SELECT e.id,e.session_id FROM goal_executions e '
                                        'JOIN goals g ON g.id=e.goal_id WHERE g.project=?', (name,)):
                    subagent_keys.setdefault(row['session_id'], []).append(row['id'])
                reports, leases, claims = board.reports(conn, name), board.leases(conn, name), board.claims(conn, name)
                member_leaders = {s['session_id']: board.leader_of(conn, s['session_id'])
                                  for s in sessions if s['role'] != 'leader'}
                for session in sessions:
                    sid = session['session_id']
                    key = process_keys[sid]
                    context = self.chat.context(key) if self.chat.running(key) else contexts.get(sid)
                    own_tasks = [t for t in tasks if t['session_id'] == sid]
                    for task_key in [sid, *subagent_keys.get(sid, [])]:
                        own_tasks.extend(self.chat.subagent_tasks(task_key))
                    own_tasks.sort(key=lambda task: task['started_at'])
                    recent_tasks = [t for t in own_tasks if t['status'] == 'running' or
                                    store.parse_iso(t.get('updated_at') or t['ended_at'] or t['started_at']) >=
                                    self.now() - dt.timedelta(hours=24)]
                    codex = dict(codex=[{'id': t.get('id') or t['run_id'] or t['rollout_id'], **{key: t[key] for key in
                                        ('task_name', 'status', 'started_at', 'ended_at', 'duration_seconds')},
                                        'source': t.get('source', 'dispatch'), 'detail': t.get('detail'),
                                        'runtime': t.get('runtime', 'codex')}
                                       for t in recent_tasks],
                                 codex_older=len(own_tasks) - len(recent_tasks))
                    if session['role'] == 'leader':
                        events = histories[sid]['events'] if sid in histories else []
                        read_seq = board.read_seq(conn, sid)
                        result['leaders'].append(dict(
                            session_id=sid, name=session['crew_name'], goal=session['task'], status=session['status'],
                            member_defaults=member_choices.get(sid),
                            active_goal_id=active_goals[sid], active_execution_id=key if key != sid else None,
                            runtime=runtimes.get(conn, sid)['runtime'],
                            runtime_switch=runtime_switch.state(conn,session,self.chat,agents,error),
                            trust=trust_mode.get(conn,name,sid),
                            is_git_admin=git_config is not None and git_config['admin_session_id'] == sid,
                            **models.get(conn, sid), applied=self.chat.applied(key), activity=self.chat.activity(key),
                            context=context, **codex,
                            where='bg' if self.chat.running(sid) or sid in present and present[sid].get('kind') == 'crew' else 'vscode' if sid in present else 'offline',
                            busy=self.chat.busy(sid) or (sid in present and present[sid].get('status') == 'busy'),
                            running=self.chat.running(sid) or sid in present, web_chat=session['web_chat'], cc_name=session['cc_name'],
                            last_seq=events[-1]['seq'] if events else 0, read_seq=read_seq,
                            last_reply_id=next((e['id'] for e in reversed(events) if e['type'] == 'assistant'), None),
                            unread=len({e['id'] for e in events if e['type'] == 'assistant' and e['seq'] > read_seq}),
                            pending=sum(d['leader_session_id'] == sid and not d['held'] for d in result['decisions']),
                            notices_pending=len(board.pending_notices(conn, sid)),
                            member_ids=[s['session_id'] for s in sessions if s['role'] != 'leader' and s['crew_name']
                                        and member_leaders[s['session_id']] is not None
                                        and member_leaders[s['session_id']]['session_id'] == sid]))
                    elif session['crew_name'] and session['group_name'] in open_groups:
                        leader = member_leaders[sid]
                        card = board.card(conn, sid)
                        result['members'].append(dict(
                            session_id=sid, name=session['crew_name'], title=titles.get(sid),
                            active_goal_id=active_goals[sid], active_execution_id=key if key != sid else None,
                            runtime=runtimes.get(conn, sid)['runtime'],
                            runtime_switch=runtime_switch.state(conn,session,self.chat,agents,error),
                            **models.get(conn, sid), applied=self.chat.applied(key), activity=self.chat.activity(key),
                            context=context, busy=self.chat.busy(sid) or (sid in present and present[sid].get('status') == 'busy'),
                            running=self.chat.running(sid) or sid in present,
                            leader_session_id=leader['session_id'] if leader else None, group=session['group_name'],
                            status=session['status'], status_note=session['status_note'],
                            where='bg' if self.chat.running(sid) or sid in present and present[sid].get('kind') == 'crew' else 'vscode' if sid in present else 'offline',
                            **{key: session[key] for key in ('task', 'branch', 'worktree', 'cwd', 'cc_name', 'web_chat')},
                            scope=card['scope'] if card else None, done_when=card['done_when'] if card else None,
                            progress=[{'at': r['created_at'], 'text': r['body']} for r in reversed(reports) if r['session_id'] == sid][:5],
                            **codex,
                            leases=[{key: lease[key] for key in ('resource', 'purpose', 'taken_at', 'until_at')}
                                    for lease in leases if lease['holder_session'] == sid],
                            claims=[{key: claim[key] for key in ('pattern', 'note')} for claim in claims if claim['session_id'] == sid]))
                result['leaders'] = board.sort_leaders(conn, name, result['leaders'])
                priorities = board.leader_priorities(conn, name, result['leaders'])
                for leader in result['leaders']:
                    leader['priority'] = priorities.get(leader['session_id'])
                result['candidates'] = self.candidates(conn, name)
                from . import decision_briefs, decision_flow
                result['decisions'] = decision_flow.enrich(conn, decision_briefs.enrich(conn, result['decisions']))
                result['attention'] = presentation.attention(conn, sessions, decisions, tasks,
                    {sid: history['events'] for sid, history in histories.items()})
            return result

    def managed_agents(self, conn, agents):
        scoped_native = self.chat.goal_controller.native_ids(conn)
        result = {a['sessionId']: dict(a) for a in agents if a['sessionId'] not in scoped_native}
        with self.chat.lock:
            for sid, current in self.chat.processes.items():
                if current.process.poll() is None:
                    logical_id = current.execution['session_id'] if current.execution else sid
                    session = board.session(conn, logical_id)
                    busy = self.chat.busy(sid) or result.get(logical_id, {}).get('status') == 'busy'
                    result[logical_id] = {'sessionId': logical_id, 'name': session['crew_name'], 'pid': current.process.pid,
                                   'cwd': session['cwd'], 'kind': 'crew',
                                   'status': 'busy' if busy else 'idle'}
        return list(result.values())

    def limits(self, conn):
        latest = conn.execute('SELECT * FROM codex_rate_limits ORDER BY ts DESC LIMIT 1').fetchone()
        codex = None
        if (latest is not None and latest['used_percent'] is not None and latest['resets_at'] is not None
                and store.parse_iso(latest['resets_at']) > self.now()):
            codex = dict(used_percent=latest['used_percent'], resets_at=latest['resets_at'], captured_at=latest['ts'], expired=False)
        return {'claude': self._claude_limits(),
                'codex': codex}

    def _claude_limits(self):
        limits = claude_limits.read(self.home, cache_path=self.claude_root.parent.parent / '.claude.json', now=self.now())
        seven_day = limits['seven_day']
        reset, crew = self.crew_usage
        # A new or expired account window must not inherit the previous week's estimate.
        if (seven_day is not None and seven_day['used_percent'] is not None
                and crew is not None and seven_day['resets_at'] == reset):
            seven_day['crew'] = crew
        return limits

    def decision_participants(self, conn, decision):
        asker = board.session(conn, decision['session_id'])
        leader_id=trust_mode.decision_leader(conn,decision['id'])
        leader=board.session(conn,leader_id) if leader_id is not None else None
        if leader is not None and (leader['project']!=decision['project'] or leader['role']!='leader'
                                  or leader['status']=='closed' or board.is_archived(conn,leader_id)):
            leader=None
        with self.agents_lock:
            if self.agents_error is None:
                live = board.live_sessions(self.agents)
                if asker['session_id'] in live:
                    asker['cc_name'] = live[asker['session_id']]['name']
        return asker, leader

    def git(self, project_name=None, offset=0):
        if offset < 0:
            raise ValueError("offset 必须大于或等于 0")
        with closing(store.connect(self.home)) as conn:
            name = self.selected_project(conn, project_name)
            project = board.project(conn, name)
            config = board.git_config(conn, name)
            sessions = board.sessions(conn, name)
            requests = board.git_requests(conn, name, all=True) if config is not None else []
        result = {"enabled": config is not None, "config": config,
                  "requests": board.titled_requests(requests, lambda sid: vscode_sessions.session_title(self.claude_root, sid)),
                  "choices": {"branches": [], "base": None, "stable": None},
                  "repository": Path(project["root"]).name, "last_fetch_at": None}
        reason = git_view.unavailable_reason(project["root"])
        if reason is not None:
            result.update(available=False, reason=reason)
            return result
        result['choices'] = git_view.branch_choices(project['root'])
        if config is not None:
            result.update(git_view.view(project["root"], config, sessions, offset=offset))
        return result

    def git_settings(self, project_name=None):
        with closing(store.connect(self.home)) as conn:
            name = self.selected_project(conn, project_name)
            result = git_admin.snapshot(conn, name)
        root = result['project']['root']
        result.update(choices={'branches': [], 'base': None, 'stable': None}, remotes=[])
        try:
            reason = git_view.unavailable_reason(root)
            if reason is not None:
                result.update(available=False, reason=reason)
                return result
            result['choices'] = git_view.branch_choices(root)
            result['remotes'] = git_view.remotes(root)
            result['available'] = True
        except (OSError, git_view.GitError, subprocess.TimeoutExpired) as exc:
            result.update(available=False, reason=str(exc))
        return result

    def git_commit(self, project_name, sha):
        with closing(store.connect(self.home)) as conn:
            name = self.selected_project(conn, project_name)
            if board.git_config(conn, name) is None:
                raise board.BoardError("请先启用 Git 管理")
            root = board.project(conn, name)["root"]
        git_view.require_repository(root)
        return git_view.commit_detail(root, sha)

    def settings_state(self):
        return {'ai': settings.get(self.home), 'execution': settings.get_execution(self.home), 'runtimes': self.runtime_catalog.snapshot(),
                'home': str(Path(self.home).resolve()), 'backend': str(Path(self.plugin_root).resolve())}

    def workspace_state(self, project_name):
        with self.chat.lock, closing(store.connect(self.home)) as conn:
            project = self.selected_project(conn, project_name)
            result = workspace_settings.snapshot(conn, project)
            with self.agents_lock:
                live = board.live_sessions(self.agents) if self.agents_error is None else {}
            sessions = []
            for row in conn.execute('SELECT * FROM sessions WHERE project=? ORDER BY role,crew_name,session_id', (project,)):
                keys = self.chat._identity_keys(conn, row['session_id'])
                running = any(self.chat.running(key) for key in keys)
                sessions.append({'session_id': row['session_id'], 'name': row['crew_name'] or row['cc_name'] or row['session_id'],
                                 'cwd': row['cwd'], 'worktree': row['worktree'],
                                 'running': running or row['session_id'] in live,
                                 'busy': any(self.chat.busy(key) for key in keys), 'status': row['status']})
            result.update(sessions=sessions, goals_count=len(goals.list_goals(conn, project)))
            return result

    def update_workspace(self, path, data, project_name):
        with self.write_lock, self.chat.lock, closing(store.connect(self.home)) as conn:
            body_project = _text(data, 'project')
            if project_name is not None and project_name != body_project:
                raise board.BoardError('请求正文和地址中的工作区不一致')
            project = self.selected_project(conn, body_project)
            revision = data.get('revision')
            if type(revision) is not int or revision < 0:
                raise ValueError('revision 必须是非负整数')
            current = workspace_settings.snapshot(conn, project)
            if current['revision'] != revision:
                raise board.Conflict('工作区设置已更改，请重新读取后再保存')
            now = self.now().isoformat(timespec='milliseconds').replace('+00:00', 'Z')
            if path == '/api/project/remove':
                if data.get('stop') is not True:
                    raise board.BoardError('移除前需明确确认停止该工作区的执行')
                workspace_execution.stop_project(self.chat, conn, project)
                workspace_settings.remove(conn, project, revision, now=now)
                if self.default_project == project:
                    self.default_project = None
                return {'ok': True, 'project': project, 'removed': True}
            display_name = workspace_settings.validate_display_name(data.get('display_name'))
            root = _text(data, 'root')
            plan = workspace_settings.plan_directory_change(conn, project, root)
            if plan['changed']:
                if data.get('stop') is not True:
                    raise board.BoardError('更改目录前需明确确认停止并迁移已有会话')
                workspace_execution.stop_project(self.chat, conn, project)
            updated = workspace_settings.apply_update(conn, project, display_name, root, revision, now=now)
            return {**self.workspace_state(project), 'directory_change': updated['directory_change']}

    def maintenance_post(self, path, data, project_name):
        body_project = _text(data, 'project')
        if project_name is not None and project_name != body_project:
            raise board.BoardError('请求正文和地址中的工作区不一致')
        with closing(store.connect(self.home)) as conn:
            project = self.selected_project(conn, body_project)
        if path == '/api/maintenance/department/preview':
            return self.maintenance.department_preview(project, data.get('leader_session_id'))
        if path == '/api/maintenance/department/compact':
            return self.maintenance.department_start(project, data.get('leader_session_id'), data.get('preview_id'), data.get('targets'))
        if path == '/api/maintenance/cleanup/preview':
            return self.maintenance.cleanup_preview(project)
        if path == '/api/maintenance/cleanup':
            return self.maintenance.cleanup_start(project, data.get('preview_id'), data.get('confirm'))
        raise board.BoardError('未知维护操作')

    def storage_post(self, path, data):
        fields = {
            '/api/storage/preview': {'categories'},
            '/api/storage/clean': {'preview_id'},
            '/api/storage/restore': {'batch_id'},
            '/api/storage/trash/empty': {'batch_id'},
            '/api/storage/policy': {'automatic_enabled', 'retention_days', 'keep_backups', 'recycle_days'},
        }
        if path not in fields or set(data) - fields[path]:
            raise ValueError('存储请求包含不支持的字段')
        if path == '/api/storage/preview':
            return self.storage_cleanup.preview(data.get('categories'))
        if path == '/api/storage/clean':
            return self.storage_cleanup.clean(_text(data, 'preview_id'))
        if path == '/api/storage/restore':
            return self.storage_cleanup.restore(_text(data, 'batch_id'))
        if path == '/api/storage/trash/empty':
            return self.storage_cleanup.empty_trash(_optional_text(data, 'batch_id'))
        return self.storage_cleanup.save_policy(data)

    def native_approvals(self, project):
        with self.chat.lock:
            return sorted([approval for current in self.chat.processes.values()
                           if getattr(current, 'runtime', 'claude') == 'codex'
                           for approval in current.pending_approvals() if approval['project'] == project],
                          key=lambda approval: (approval['created_at'], approval['id']))

    def resolve_native_approval(self, conn, project, identity, allow):
        if type(allow) is not bool:
            raise ValueError('allow 必须是 true 或 false')
        with self.chat.lock:
            for key, current in self.chat.processes.items():
                if getattr(current, 'runtime', 'claude') != 'codex':
                    continue
                approval = next((item for item in current.pending_approvals()
                                 if item['id'] == identity and item['project'] == project), None)
                if approval is None:
                    continue
                if not self.chat.running(key) or messaging.stopped(conn, approval['session_id']):
                    raise board.Conflict('该原生执行已停止，审批已经失效')
                if approval['execution_id'] is not None:
                    gate = goals.dispatch_check(conn, approval['goal_id'], execution_id=approval['execution_id'])
                    if not gate['allowed']:
                        raise board.Conflict(gate['reason'])
                return {'approval': current.resolve_approval(identity, allow)}
        raise board.Conflict('审批已结束或不属于当前工作区，请刷新')

    def post(self, path, data, project_name=None):
        if not isinstance(data, dict):
            raise ValueError("JSON \u8bf7\u6c42\u5fc5\u987b\u662f\u5bf9\u8c61")
        if path == "/api/admin/update/prepare":
            return self.prepare_update(data)
        if path == "/api/admin/update/cancel":
            return self.cancel_update(data)
        if path == '/api/admin/connection/prepare':
            return self.prepare_connection(data)
        if path in MODEL_WORK or path.startswith("/api/goals/") or path in ("/api/native-approval", "/api/decision/discuss", "/api/decision/clarify", "/api/project/update", "/api/project/remove", "/api/maintenance/cleanup"):
            with self.chat.work():
                return self._post(path, data, project_name)
        return self._post(path, data, project_name)

    def _post(self, path, data, project_name):
        if path.startswith('/api/storage/'):
            return self.storage_post(path, data)
        if path in ('/api/project/update', '/api/project/remove'):
            return self.update_workspace(path, data, project_name)
        if path.startswith('/api/maintenance/'):
            return self.maintenance_post(path, data, project_name)
        if path == '/api/admin/chat/send':
            return operations_chat.send(self.home, self.plugin_root, data)
        if path == '/api/admin/chat/interrupt':
            return operations_chat.interrupt(self.home)
        if path == '/api/admin/config':
            return operations_settings.save(self.home, self.plugin_root, data)
        if path == '/api/connections/configure':
            return self.connections.configure(data)
        if path == '/api/connections/action':
            return self.connections.action(data)
        if path == '/api/connections/preferences':
            return self.connections.save_preferences(data)
        if path == '/api/member-defaults':
            if set(data) != {'leader_session_id', 'selection'}:
                raise ValueError('组员默认设置必须包含 leader_session_id 和 selection')
            with self.write_lock, closing(store.connect(self.home)) as conn:
                project = self.selected_project(conn, project_name)
                sid = _text(data, 'leader_session_id')
                if board.session(conn, sid)['project'] != project:
                    raise board.BoardError('leader 不属于当前工作区')
                return member_defaults.save(conn, sid, data['selection'], now=self.now().isoformat(timespec='milliseconds').replace('+00:00', 'Z'))
        if path == '/api/settings':
            if not data or not set(data) <= {'ai', 'execution'}:
                raise ValueError('设置请求只能包含 ai 和 execution 配置')
            with self.write_lock, closing(store.connect(self.home)) as conn, goals.transaction(conn):
                if 'ai' in data:
                    settings.save(conn, data['ai'])
                if 'execution' in data:
                    settings.save_execution(conn, data['execution'])
            return self.settings_state()
        if path in ('/api/session/runtime/prepare','/api/session/runtime/switch'):
            with closing(store.connect(self.home)) as conn:
                project=self.selected_project(conn,project_name if project_name is not None else data.get('project'))
                target=board.session(conn,_text(data,'session_id'))
                if target['project']!=project or data.get('project',project)!=project:
                    raise board.BoardError('会话不属于当前工作区')
                if path.endswith('/prepare'):
                    desired=runtimes.validate(data.get('runtime'))
                else:
                    receipt=conn.execute('SELECT to_runtime,status FROM runtime_switches WHERE id=? AND session_id=? AND project=?',(_text(data,'preview_id'),target['session_id'],project)).fetchone()
                    if receipt is None:raise board.Conflict('更换预览已失效，请重新检查')
                    if receipt['status']=='configured':
                        return self.chat.switch_runtime(conn,target['session_id'],data['preview_id'],data.get('confirm'),[],now=self.now().isoformat(timespec='milliseconds').replace('+00:00','Z'))
                    desired=receipt['to_runtime']
            catalog=self.runtime_catalog.refresh(force=True) if desired=='codex' else self.runtime_catalog.snapshot()
            with self.write_lock,closing(store.connect(self.home)) as conn:
                stamp=self.now().isoformat(timespec='milliseconds').replace('+00:00','Z')
                if path.endswith('/prepare'):
                    return self.chat.prepare_runtime(conn,target['session_id'],desired,catalog,model=data.get('model'),effort=data.get('effort'),handoff=data.get('handoff'),now=stamp)
                return self.chat.switch_runtime(conn,target['session_id'],data['preview_id'],data.get('confirm'),catalog,now=stamp)
        if path in ("/api/files/resolve", "/api/files/open", "/api/files/reveal"):
            if project_name is not None and data.get('project') is not None and data['project'] != project_name:
                raise board.BoardError('请求正文和地址中的项目不一致')
            info = self.resolve_file(data.get("session_id"), None if 'document_id' in data else _text(data, "path"),
                                     project_name if project_name is not None else data.get("project"), data.get("base"), data.get('document_id'))
            if path == "/api/files/resolve":
                return info
            if info["error"] is not None:
                raise board.BoardError(info["error"])
            if path == "/api/files/open":
                if info["open_with"] == "blocked":
                    raise board.BoardError("这类文件不直接打开，请用‘在文件夹中显示’")
                target = ("vscode://file/" + quote(Path(info["path"]).as_posix(), safe="/:")
                          if info["open_with"] == "vscode" else info["path"])
                os.startfile(target)
            else:
                subprocess.Popen(["explorer", "/select," + info["path"]])
            return {"ok": True}
        if path == "/api/git/fetch":
            with closing(store.connect(self.home)) as conn:
                project = self.selected_project(conn, project_name if project_name is not None else data.get("project"))
                if board.git_config(conn, project) is None:
                    raise board.BoardError("请先启用 Git 管理")
                root = board.project(conn, project)["root"]
            git_view.require_repository(root)
            return git_view.fetch(root)
        with self.write_lock, closing(store.connect(self.home)) as conn:
            now = self.now().isoformat(timespec="milliseconds").replace("+00:00", "Z")
            if path == "/api/project/init":
                root = Path(_text(data, "root")).expanduser().resolve()
                if not root.is_dir():
                    raise board.BoardError("项目目录不存在：" + str(root))
                result = board.init_project(conn, _text(data, "name"), root, now=now)
                self.default_project = result["name"]
                return result
            if path == '/api/notifications/away':
                if type(data.get('away')) is not bool:
                    raise ValueError('离开状态必须是 true 或 false')
                return self.set_away(data['away'])
            if path in ('/api/channel', '/api/channel/read', '/api/channel/digest', '/api/project/theme', '/api/leaders/order', '/api/leaders/priority', '/api/documents', '/api/documents/pin', '/api/documents/refresh', '/api/native-approval') and 'project' in data:
                body_project = _text(data, 'project')
                if project_name is not None and body_project != project_name:
                    raise board.BoardError('请求正文和地址中的项目不一致')
                project_name = body_project
            if path == '/api/git/settings':
                body_project = _text(data, 'project')
                if project_name is not None and project_name != body_project:
                    raise board.BoardError('请求正文和地址中的项目不一致')
                return git_admin.configure(conn, self.selected_project(conn, body_project), data, now=now)
            if path in ("/api/git/enable", "/api/git/disable"):
                project = self.selected_project(conn, project_name if project_name is not None else data.get("project"))
                if path == "/api/git/enable":
                    stable = data.get("stable")
                    if stable is not None and not isinstance(stable, str):
                        raise ValueError("stable 必须是分支名或 null")
                    return git_admin.enable(conn, self.home, self.plugin_root, project, self.chat.start,
                                            base=_optional_text(data, "base"), stable=stable, now=now,
                                            runtime=data.get("runtime"))
                result = git_admin.disable(conn, project, now=now)
                if 'before' in result:
                    self.stop_archived(result['before'])
                return {'ok': True}
            project = self.selected_project(conn, project_name)
            if path == '/api/native-approval':
                return self.resolve_native_approval(conn, project, _text(data, 'id'), data.get('allow'))
            if path.startswith('/api/goals/'):
                return goal_api.post(self, conn, project, path, data)
            if path in ('/api/documents', '/api/documents/pin', '/api/documents/refresh'):
                if 'project' in data and data['project'] != project:
                    raise board.BoardError('请求正文和地址中的项目不一致')
                if path == '/api/documents':
                    return documents.register(conn, _text(data,'session_id'), _text(data,'path'),
                        title=data.get('title'), summary=data.get('summary'), kind=data.get('kind','other'),
                        important=data.get('important',False), project=project, now=now,
                        goal_id=data.get('goal_id'), execution_id=data.get('execution_id'))
                if path == '/api/documents/pin':
                    if 'important' not in data: raise ValueError('缺少 important 字段')
                    return documents.pin(conn,project,data.get('id'),data['important'],now=now)
                scan=documents.refresh(conn,self.home,project,now=now)
                return {'documents':documents.list(conn,project), 'scan':scan}
            if path == '/api/trust':
                return trust_mode.set(conn, project, data.get('enabled'), data.get('goal'), now=now,leader_session_id=_text(data,'leader_session_id'))
            if path in ('/api/decision/discuss', '/api/decision/ready'):
                from . import decision_flow
                if type(data.get('id')) is not int:
                    raise ValueError('id 必须是整数')
                if path.endswith('/ready'):
                    return decision_flow.ready(conn, data['id'], project=project, now=now)
                result = decision_flow.discuss(conn, data['id'], project=project, now=now)
                from . import goal_links
                link = goal_links.get(conn, 'decision', data['id'])
                linked_goal = goals.get(conn, link['goal_id']) if link else None
                conversation_goal = linked_goal['id'] if linked_goal and linked_goal['status'] in ('queued', 'running') and not linked_goal['manual_pause'] else None
                notice = conn.execute('SELECT * FROM leader_notices WHERE id=?', (result.get('notice_id'),)).fetchone()
                if notice is not None and notice['sent_at'] is None:
                    target = board.session(conn, result['leader_session_id'])
                    if conversation_goal:
                        self.chat.goal_controller.send(conversation_goal, target['session_id'], notice['text'],
                                                       source='human', identity='decision-discuss:' + str(data['id']))
                    else:
                        if not self.chat.path(target['session_id']).exists():
                            self.chat.journal(target['session_id']).set_status('idle')
                        text = ('仅解释这项选择和后果；关联目标当前未运行，保持其状态，不能据此恢复施工。\n' if linked_goal else '') + notice['text']
                        self.chat.send(target, text, source='human', identity='decision-discuss:' + str(data['id']))
                    board.mark_notice_sent(conn, notice['id'], now=now)
                return {**result, 'conversation_goal_id': conversation_goal}
            if path == '/api/decision/clarify':
                from . import decision_briefs
                if type(data.get('id')) is not int:
                    raise ValueError('id 必须是整数')
                item = conn.execute('SELECT project FROM decisions WHERE id=?', (data['id'],)).fetchone()
                if item is None or item['project'] != project:
                    raise board.BoardError('拍板不属于当前工作区')
                identity = decision_briefs.queue_review(conn, data['id'], now=now)
                return {'ok': True, 'notice_id': identity, 'message': '已请 leader 重新说明这项选择'}
            if path == '/api/comments':
                sid, text = _text(data, 'session_id'), _text(data, 'text')
                session = board.session(conn, sid)
                if session['project'] != project or session['status'] == 'closed' or board.is_archived(conn, sid):
                    raise board.BoardError('评论对象不属于当前工作区或已归档')
                if len(text) > 10000:
                    raise ValueError('评论不能超过 10000 字')
                with board.transaction(conn):
                    identity = conn.execute('INSERT INTO workspace_comments(project,session_id,text,created_at) VALUES(?,?,?,?)',
                                            (project, sid, text, now)).lastrowid
                    leader = session if session['role'] == 'leader' else board.leader_of(conn, sid)
                    if leader is not None:
                        board._queue_leader_notice(conn, leader['session_id'],
                            '用户在总览中给「%s」的评论：\n%s' % (session['crew_name'], text), 'web', now=now)
                return dict(conn.execute('SELECT * FROM workspace_comments WHERE id=?', (identity,)).fetchone())
            if path in ('/api/todo/resolve', '/api/todo/undo'):
                if type(data.get('id')) is not int:
                    raise ValueError('id 必须是整数')
                if path.endswith('/undo'):
                    return todo.undo(conn, project, data['id'], data.get('before'), data.get('version'), now=now)
                return todo.resolve(conn, project, data['id'], _text(data, 'status'),
                                    note=data.get('note'), expected_version=data.get('expected_version'), now=now)
            if path == '/api/messages':
                caller = board.session(conn, _text(data, 'caller_session_id'))
                if caller['project'] != project:
                    raise board.BoardError('发件人不属于当前工作区')
                name = _text(data, 'name')
                row = conn.execute('SELECT session_id FROM sessions WHERE session_id=? AND project=?', (name, project)).fetchone()
                target = row['session_id'] if row else board.named_session(conn, project, name)['session_id']
                return self.chat.send_message(caller['session_id'], target, _text(data, 'text'), data.get('message_id'),
                                              execution_id=data.get('execution_id'), kind=data.get('kind', 'information'))
            if path == '/api/leaders/order':
                _text(data, 'project')
                return board.set_leader_order(conn, project, data.get('order'))
            if path == '/api/leaders/priority':
                _text(data, 'project')
                return board.set_leader_priority(conn, project, data.get('ranks'))
            if path == '/api/project/theme':
                return board.set_project_theme(conn, project, _text(data, 'theme'), now=now)
            if path == '/api/channel':
                message = channel.post(conn, project, board.HUMAN, _text(data, 'body'),
                                       data.get('to', []), source='web', now=now)
                return {'message': message}
            if path == '/api/channel/read':
                return channel.mark_read(conn, project, board.HUMAN, data['id'], now=now)
            if path == '/api/channel/digest':
                _text(data, 'project')
                self.channel_digest.start(conn, project)
                return {'ok': True}
            if path == '/api/attention/dismiss':
                identity = _text(data, 'id')
                kind, _, rest = identity.partition(':')
                if kind == 'decision':
                    owner = conn.execute('SELECT project FROM decisions WHERE id=?', (rest,)).fetchone()
                elif kind in ('waiting', 'error'):
                    owner = conn.execute('SELECT project FROM sessions WHERE session_id=?', (rest.split(':', 1)[0],)).fetchone()
                elif kind == 'codex':
                    owner = (project,) if any((t['run_id'] or t['rollout_id']) == rest for t in usage.codex_tasks(conn, None, None, project=project)) else None
                else:
                    owner = None
                if owner is None or owner[0] != project:
                    raise board.BoardError('待处理事项不属于当前工作区')
                if data.get('dismiss') is False:
                    conn.execute('DELETE FROM attention_resolutions WHERE item_id=?', (identity,))
                else:
                    conn.execute('INSERT OR REPLACE INTO attention_resolutions VALUES(?,?)', (identity, now))
                return {'ok': True}
            if path.startswith('/api/session/'):
                target = board.session(conn, _text(data, 'session_id'))
                if target['project'] != project:
                    raise board.BoardError('会话不属于当前工作区')
                if path == '/api/session/rename':
                    result = session_ops.rename(conn, target['session_id'], _text(data, 'name'), now=now)
                    result['native_warnings'] = self.chat.sync_native_titles(conn, target['session_id'])
                    return result
                if path == '/api/session/archive':
                    return self.archive_session(conn, target['session_id'], now=now)
                if path == '/api/session/remove':
                    return self.archive_session(conn, target['session_id'], removed=True, now=now)
                if path == '/api/session/restore':
                    return session_ops.restore(conn, target['session_id'], now=now)
                if path == '/api/session/undo':
                    return session_ops.undo(conn, target['session_id'], data['before'], now=now)
                if path == '/api/session/stop':
                    return self.chat.stop_identity(target['session_id'], caller_session_id=_optional_text(data, 'caller_session_id'))
                if path == '/api/session/compact':
                    return self.chat.compact(target, caller_session_id=_optional_text(data, 'caller_session_id'))
                if path == '/api/session/options':
                    options = self.chat.set_options(conn, target['session_id'],
                                                    {key: data[key] for key in ('model', 'effort') if key in data}, now=now)
                    return {'ok': True, **options}
            if path in ("/api/answer", "/api/decision/hold", "/api/decision/unhold"):
                if type(data.get("id")) is not int:
                    raise ValueError("id \u5fc5\u987b\u662f\u6574\u6570")
                decision = conn.execute("SELECT * FROM decisions WHERE id=?", (data["id"],)).fetchone()
                if decision is None:
                    raise board.BoardError("待拍板不存在: " + str(data["id"]))
                if decision["project"] != project:
                    raise board.BoardError("待拍板不属于当前项目")
                if path == "/api/decision/unhold":
                    board.unhold(conn, data["id"], now=now)
                    return {"ok": True}
                if path == "/api/decision/hold":
                    if board.hold(conn, data["id"], now=now):
                        asker, leader = self.decision_participants(conn, decision)
                        if leader is not None:
                            notice = ('拍板 #%s：%s（SendMessage 地址：%s）\n问题：%s\n'
                                      '用户挂起了，暂时不拍。请用 SendMessage 告诉提问的组员：'
                                      '不卡流程的按推荐继续；卡流程的先做别的，先别再问这道题。' %
                                      (data['id'], asker['crew_name'], asker['cc_name'], decision['question']))
                            if any(runtimes.get(conn, s['session_id'])['runtime'] == 'codex' for s in (asker, leader)):
                                notice = ('拍板 #%s：%s\n问题：%s\n用户挂起了，暂时不拍。请用 crew send %s "<处理安排>" 转告：'
                                          '不卡流程的按推荐继续；卡流程的先做别的，先别再问这道题。' %
                                          (data['id'], asker['crew_name'], decision['question'], asker['crew_name']))
                            board.queue_leader_notice(conn, leader['session_id'], notice, 'web', now=now)
                    return {"ok": True}
                text = _text(data, 'text')
                board.answer(conn, data["id"], text, board.HUMAN, now=now)
                from . import goal_links
                link = goal_links.get(conn, 'decision', data['id'])
                if link:
                    return {'ok': True, 'goal_id': link['goal_id'], 'routed_to': decision['session_id']}
                asker, leader = self.decision_participants(conn, decision)
                if leader is not None:
                    notice = ('拍板 #%s：%s（SendMessage 地址：%s）\n问题：%s\n答复：%s\n'
                              '请马上用 SendMessage 转告并叫它继续' %
                              (data['id'], asker['crew_name'], asker['cc_name'], decision['question'], text))
                    if any(runtimes.get(conn, s['session_id'])['runtime'] == 'codex' for s in (asker, leader)):
                        action = ('请按用户答复继续。' if asker['session_id'] == leader['session_id'] else
                                  '请用 crew send %s "<答复与后续安排>" 转告；离线时用 crew wake 接续原会话。' % asker['crew_name'])
                        notice = ('拍板 #%s：%s\n问题：%s\n答复：%s\n%s' %
                                  (data['id'], asker['crew_name'], decision['question'], text, action))
                    board.queue_leader_notice(conn, leader['session_id'], notice, 'web', now=now)
                return {"ok": True, 'routed_to': leader['crew_name'] if leader else None}
            if path in ("/api/open", "/api/open/continue"):
                target = (board.session(conn, _text(data, "session_id")) if path.endswith("/continue") or data.get("session_id") is not None else
                          board.named_session(conn, project, _text(data, "name")))
                if target["project"] != project:
                    raise board.BoardError("会话不属于当前项目")
                if path == "/api/open":
                    return open_session.begin(conn, target["session_id"], self.agents_provider,
                                              self.claude_root.parent / "sessions", manager=self.chat)
                return open_session.resume(conn, target["session_id"], self.agents_provider, data.get("confirm") is True,
                                           self.claude_root.parent / "sessions", now=now, manager=self.chat)
            if path == "/api/wake":
                target = (board.session(conn, _text(data, "session_id")) if data.get("session_id") is not None else
                          board.named_session(conn, project, _text(data, "name")))
                if target["project"] != project:
                    raise board.BoardError("会话不属于当前项目")
                with self.agents_lock:
                    if (target['role'] != 'leader' and runtimes.get(conn, target['session_id'])['runtime'] == 'claude' and
                            (not self.agents_ready.is_set() or self.agents_error is not None)):
                        raise board.Conflict("进程状态未知，请稍后再试")
                return self.chat.wake(conn, target, _text(data, "text"),
                                      caller_session_id=_optional_text(data, "caller_session_id"), now=now)
            if path == "/api/spawn":
                group = _optional_text(data, 'group')
                leader_id = _optional_text(data, 'leader_session_id')
                if leader_id is not None:
                    parent = board.session(conn, leader_id)
                    active_group = conn.execute('SELECT name FROM groups WHERE project=? AND leader_session_id=? AND closed_at IS NULL', (project, leader_id)).fetchone()
                    if (parent['project'] != project or parent['role'] != 'leader' or parent['status'] == 'closed'
                            or board.is_archived(conn, leader_id) or active_group is None or data.get('role', 'member') != 'member'):
                        raise board.BoardError('请选择当前工作区尚未结束的 leader')
                    if group is not None and group != active_group['name']:
                        raise board.BoardError('所选 leader 与分组不一致，请刷新后重试')
                    group = active_group['name']
                return self.chat.start(conn, project, _text(data, "name"), _text(data, "task"),
                                       group=group, role=data.get("role", "member"),
                                       cwd=_optional_text(data, "cwd"), new_worktree=_optional_text(data, "new_worktree"),
                                       base=_optional_text(data, "base"), now=now,
                                       caller_session_id=_optional_text(data, 'caller_session_id'),
                                       caller_execution_id=_optional_text(data, 'caller_execution_id'),
                                       model=data["model"] if "model" in data else models.INHERIT,
                                       effort=data["effort"] if "effort" in data else models.INHERIT,
                                       runtime=data["runtime"] if "runtime" in data else models.INHERIT)
            if path == "/api/leader":
                members = data.get("members", [])
                if not isinstance(members, list) or any(not isinstance(m, str) or not m.strip() for m in members):
                    raise ValueError("members \u5fc5\u987b\u662f\u540d\u5b57\u6570\u7ec4")
                member_session_ids = data.get("member_session_ids", [])
                if not isinstance(member_session_ids, list) or any(not isinstance(sid, str) or not sid.strip() for sid in member_session_ids):
                    raise ValueError("member_session_ids 必须是会话 ID 数组")
                name, task = _text(data, "name"), _optional_text(data, "task")
                selected = settings.resolve(conn, 'leader') if data.get('runtime') is None else {'runtime': runtimes.validate(data['runtime']), 'model': None, 'effort': None}
                runtime = selected['runtime']
                model = models.validate(data['model'] if 'model' in data else selected['model'], runtime=runtime)
                effort = models.validate_effort(data['effort'] if 'effort' in data else selected['effort'], runtime=runtime)
                candidate_members = [(self.candidate(conn, project, _text(member, "session_id")), _text(member, "name"))
                                     for member in data.get("candidate_members", [])]
                names = {name, *members}
                for _, member_name in candidate_members:
                    board.available_name(conn, project, member_name)
                    if member_name in names:
                        raise board.Conflict("组员重名: " + member_name)
                    names.add(member_name)
                # Check the leader and existing members before adopting anyone.
                spawn.leader_plan(conn, self.home, self.plugin_root, project, name, members,
                                  task, cwd=_optional_text(data, "cwd"), new_worktree=_optional_text(data, "new_worktree"), now=now,
                                  member_session_ids=member_session_ids, model=model, effort=effort, runtime=runtime)
                for candidate, member_name in candidate_members:
                    self.adopt(conn, project, candidate, member_name, None, now)
                return self.chat.start(conn, project, name, task, role="leader",
                                       members=members, member_session_ids=member_session_ids + [candidate["session_id"] for candidate, _ in candidate_members],
                                       cwd=_optional_text(data, "cwd"),
                                       new_worktree=_optional_text(data, "new_worktree"), now=now, model=model, effort=effort, runtime=runtime)
            if path == "/api/chat/send":
                session = board.session(conn, _text(data, 'session_id'))
                if session['project'] != project:
                    raise board.BoardError('会话不属于当前工作区')
                text, images = data.get('text', ''), data.get('images', [])
                if not isinstance(text, str) or (not text.strip() and not images):
                    raise ValueError('请提供文字或图片')
                if data.get('goal_id'):
                    goals.get(conn, data['goal_id'], project)
                    return self.chat.goal_controller.send(data['goal_id'], session['session_id'], text,
                                                          images=images, identity=data.get('message_id'))
                if not self.chat.path(session['session_id']).exists():
                    self.chat.journal(session['session_id']).set_status('idle')
                return self.chat.send(session, text, images=images, identity=data.get('message_id'))
            if path == '/api/chat/interrupt':
                target = board.session(conn, _text(data, 'session_id'))
                if target['project'] != project:
                    raise board.BoardError('会话不属于当前工作区')
                if data.get('goal_id'):
                    goals.get(conn, data['goal_id'], project)
                    execution = self.chat.goal_controller.current(conn, data['goal_id'], target['session_id'])
                else:
                    execution = self.chat.goal_controller.routing_execution(conn, target['session_id'])
                if execution:
                    return self.chat.interrupt(execution['id'])
                if not data.get('goal_id'):
                    return self.chat.interrupt(target['session_id'])
                return {'ok': True, 'message': '当前没有运行中的会话'}
            if path == '/api/chat/read':
                if data.get('goal_id'):
                    goals.get(conn, data['goal_id'], project)
                    execution = self.chat.goal_controller.current(conn, data['goal_id'], _text(data, 'session_id'))
                    return {'read_seq': goals.mark_read(conn, execution['id'], data.get('seq'), now=now) if execution else 0}
                session = self.chat_session(conn, project, _text(data, 'session_id'))
                board.mark_read(conn, session['session_id'], data.get('seq'), now=now)
                return {'read_seq': board.read_seq(conn, session['session_id'])}
            if path == "/api/roster/move":
                before = session_ops.snapshot(conn, data['session_id']) if data.get('session_id') else None
                board.roster(conn, project, "move", _text(data, "to"),
                             [] if data.get("session_id") is not None else [_text(data, "name")], now=now,
                             session_ids=[_text(data, "session_id")] if data.get("session_id") is not None else [])
                if before:
                    return {'ok': True, 'before': before, 'session': board.session(conn, data['session_id'])}
            elif path == "/api/roster/remove":
                before = session_ops.snapshot(conn, data['session_id']) if data.get('session_id') else None
                board.roster(conn, project, "remove", _text(data, "group"),
                             [] if data.get("session_id") is not None else [_text(data, "name")], now=now,
                             session_ids=[_text(data, "session_id")] if data.get("session_id") is not None else [])
                if before:
                    return {'ok': True, 'before': before, 'session': board.session(conn, data['session_id'])}
            elif path == "/api/group/close":
                result = session_ops.close_group(conn, project, _text(data, "group"), with_members=data.get("with_members") is True, now=now)
                self.stop_archived(result['before'], with_members=data.get('with_members') is True)
                return result
            elif path == "/api/group/undo":
                return session_ops.undo_group(conn, project, _text(data, 'group'), data['before'], now=now)
            elif path == "/api/roster/adopt":
                candidate = self.candidate(conn, project, _text(data, "session_id"))
                return self.adopt(conn, project, candidate, _text(data, "name"), _optional_text(data, "group"), now)
            elif path == '/api/session-library/adopt':
                return session_library.adopt(self, conn, project, _text(data, 'key'),
                                             _text(data, 'name'), _optional_text(data, 'group'), now)
            return {"ok": True}

    def archive_session(self, conn, session_id, removed=False, now=None):
        result = session_ops.archive(conn, session_id, removed=removed, now=now)
        self.chat.stop_managed(session_id)
        return result

    def stop_archived(self, before, with_members=True):
        for entry in before['sessions']:
            if with_members or entry['session_id'] == before['group']['leader_session_id']:
                self.chat.stop_managed(entry['session_id'])

    def chat_session(self, conn, project, session_id):
        session = board.session(conn, session_id)
        if session["project"] != project or not session['crew_name'] or session['status'] == 'closed' or board.is_archived(conn, session_id):
            raise board.BoardError("当前项目中没有这个网页会话")
        return session

    def chat_history(self, session_id, project_name=None, goal_id=None):
        with closing(store.connect(self.home)) as conn:
            if goal_id:
                goals.get(conn, goal_id, self.selected_project(conn, project_name))
                return self.chat.goal_controller.history(conn, goal_id, session_id)
            self.chat_session(conn, self.selected_project(conn, project_name), session_id)
            return {**self.chat.history(session_id), 'read_seq': board.read_seq(conn, session_id)}

    def channel_messages(self, project_name=None, limit=20, before=None):
        with closing(store.connect(self.home)) as conn:
            project = self.selected_project(conn, project_name)
            return {**channel.messages(conn, project, limit=limit, before=before), 'digest': channel.digest(conn, project)}

    def document_list(self, project_name=None, leader_session_id=None, goal_id=None):
        with self.write_lock, closing(store.connect(self.home)) as conn:
            project=self.selected_project(conn,project_name)
            if goal_id is not None:
                goals.get(conn, goal_id, project)
            try:
                scan=documents.refresh(conn,self.home,project)
            except (OSError, sqlite3.Error, ValueError) as exc:
                scan={'indexed':0,'scanned':0,'has_more':False,'errors':[{'source':'index','error':str(exc)}]}
            return {'documents':documents.list(conn,project,leader_session_id,goal_id=goal_id), 'scan':scan}

    def resolve_file(self, session_id, text, project_name=None, base=None, document_id=None):
        with closing(store.connect(self.home)) as conn:
            project = self.selected_project(conn, project_name)
            if document_id is not None:
                document=documents.get(conn,project,document_id)
                session_id=document['session_id']
                text=document['resolved_path']
                if text is None:
                    raise board.BoardError(document['file_error'])
                base=None
            root = Path(board.project(conn, project)["root"])
            allowed_roots=[root.resolve()]
            if document_id is not None:
                allowed_roots = workspace_settings.read_roots(conn, project, session_id)
            if session_id:
                session = board.session(conn, session_id)
                if session["project"] != project:
                    raise board.BoardError("\u4f1a\u8bdd\u4e0d\u5c5e\u4e8e\u5f53\u524d\u9879\u76ee")
                cwd = Path(session["cwd"])
                if session.get('worktree'):
                    allowed_roots.append(Path(session['worktree']).resolve())
            else:
                cwd = root
        text = _text({'path':text},'path')
        if base is not None and (not isinstance(base,str) or not any(store.is_under(Path(base).resolve(),allowed) for allowed in allowed_roots)):
            raise board.BoardError('文件来源不在当前项目或会话工作区内')
        path = Path(text.replace("\\", "/"))
        if not path.is_absolute():
            path = (Path(base).parent if base is not None else cwd) / path
            if base is None and not path.exists():
                path = root / text.replace("\\", "/")
        path = path.resolve()
        if not any(store.is_under(path,allowed) for allowed in allowed_roots):
            raise board.BoardError('文件路径不在当前项目或会话工作区内')
        exists = path.exists()
        error = "\u6587\u4ef6\u4e0d\u5b58\u5728\uff1a" + str(path) if not exists else "\u8fd9\u662f\u76ee\u5f55\uff1a" + str(path) if path.is_dir() else None
        suffix = path.suffix.lower()
        images = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
        text_documents = {".md", ".markdown", ".rst", ".adoc", ".txt", ".log", ".json", ".csv", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg"}
        external_documents = {".html", ".htm", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".rtf", ".odt", ".ods", ".odp"}
        code = {".py", ".js", ".cjs", ".mjs", ".ts", ".tsx", ".jsx", ".css", ".scss", ".ps1", ".bat", ".cmd", ".sh", ".c", ".h", ".cpp", ".hpp", ".cs", ".java", ".go", ".rs", ".rb", ".php", ".sql", ".vue", ".svelte", ".uproject", ".uplugin"}
        open_with = ("default" if suffix in text_documents | external_documents | images else
                     "vscode" if suffix in code else "blocked")
        kind = "image" if suffix in images else "markdown" if suffix in (".md", ".markdown") else "text" if suffix in text_documents | code else "external"
        result={"path": str(path), "exists": exists, "kind": kind,
                "type": "external" if kind == "external" else "preview",
                "open_with": open_with, "error": error}
        if document_id is not None:
            result.update(document_id=document_id,session_id=session_id)
        return result

    def read_file(self, info, raw=False):
        if info["error"] is not None:
            raise board.BoardError(info["error"])
        path = Path(info["path"])
        if info["kind"] == "image" and raw:
            return path.read_bytes(), mimetypes.guess_type(path)[0]
        if info["kind"] not in ("text", "markdown"):
            raise board.BoardError("\u6b64\u7c7b\u578b\u8bf7\u7528\u9ed8\u8ba4\u7a0b\u5e8f\u6253\u5f00")
        limit = 256 * 1024
        size = path.stat().st_size
        tail = path.suffix.lower() == ".log"
        with path.open("rb") as stream:
            if tail and size > limit:
                stream.seek(size - limit)
            content = stream.read(limit)
        return {**info, "content": content.decode("utf-8-sig", errors="replace"),
                "truncated": size > limit, "portion": "\u672b\u5c3e" if tail else "\u5f00\u5934"}, "application/json; charset=utf-8"

    def chat_file(self, session_id, name, project_name=None, goal_id=None):
        with closing(store.connect(self.home)) as conn:
            if goal_id:
                goals.get(conn, goal_id, self.selected_project(conn, project_name))
                execution = self.chat.goal_controller.current(conn, goal_id, session_id)
                if execution is None:
                    raise board.BoardError('此目标还没有会话附件')
                session_id = execution['id']
            else:
                self.chat_session(conn, self.selected_project(conn, project_name), session_id)
            path, media_type = self.chat.file(session_id, name)
            return path.read_bytes(), media_type

    def scan_usage(self):
        with self.write_lock, self.scan_lock, closing(store.connect(self.home)) as conn:
            now = self.now()
            if self.scanned_at is None or (now - self.scanned_at).total_seconds() >= 60:
                usage.scan(conn, self.claude_root, self.codex_root, self.runs_file)
                seven_day = claude_limits.read(self.home, cache_path=self.claude_root.parent.parent / '.claude.json', now=now)['seven_day']
                crew = None
                if seven_day is not None and seven_day['used_percent'] is not None and seven_day['resets_at'] is not None:
                    crew = usage.crew_weekly(conn, seven_day, usage.load_prices(self.home / 'prices.json'), now)
                self.crew_usage = (seven_day['resets_at'] if seven_day is not None else None, crew)
                self.scanned_at = now

    def notification_status(self):
        return self.connections.notification_status()

    def set_away(self, away):
        return self.connections.set_away(away)

    def usage(self, period="week", what=None, project_name=None, scope='workspace'):
        try:
            start, end = usage.period(period, self.now())
        except ValueError as exc:
            raise ValueError("\u65f6\u95f4\u6bb5\u975e\u6cd5: " + period) from exc
        if scope not in ('workspace', 'machine'):
            raise ValueError('用量范围必须是 workspace 或 machine')
        self.scan_usage()
        with closing(store.connect(self.home)) as conn:
            project = board.project(conn, self.selected_project(conn, project_name)) if scope == 'workspace' else None
            name = project['name'] if project else None
            if what == "codex":
                return usage.codex_tasks(conn, start, end, project=name)
            prices = usage.load_prices(self.home / "prices.json")
            functions = {"summary": usage.summary, "sessions": usage.by_session, "daily": usage.daily,
                         "agents": usage.by_agent_type, "models": usage.by_model, "requests": usage.requests, 'tree': usage.tree}
            if what is not None:
                return functions[what](conn, start, end, prices, project=name)
            result = {key: fn(conn, start, end, prices, project=name) for key, fn in functions.items() if key != "requests"}
            result['summary']['claude_limits'] = self._claude_limits()
            result["codex"] = usage.codex_tasks(conn, start, end, project=name)
            result.update(scope=scope, project=project)
            return result


POST_PATHS = {"/api/project/init", "/api/project/theme", "/api/open", "/api/open/continue", "/api/answer", "/api/spawn", "/api/leader", "/api/wake", "/api/roster/move", "/api/roster/remove", "/api/group/close", "/api/group/undo", "/api/roster/adopt", "/api/chat/send", '/api/chat/read'}
POST_PATHS.update({'/api/messages', '/api/todo/resolve', '/api/todo/undo'})
POST_PATHS.update({'/api/goals/create', '/api/goals/action', '/api/goals/context', '/api/goals/agent', '/api/chat/interrupt', '/api/decision/discuss', '/api/decision/ready'})
POST_PATHS.update({'/api/trust', '/api/comments', '/api/decision/clarify'})
POST_PATHS.update({'/api/project/update', '/api/project/remove'})
POST_PATHS.update('/api/session/' + action for action in ('rename', 'archive', 'remove', 'restore', 'undo', 'stop', 'options', 'compact'))
POST_PATHS.update(('/api/session/runtime/prepare','/api/session/runtime/switch'))
POST_PATHS.update(('/api/attention/dismiss', '/api/notifications/away'))
POST_PATHS.update(('/api/decision/hold', '/api/decision/unhold'))
POST_PATHS.update(('/api/channel', '/api/channel/read', '/api/channel/digest'))
POST_PATHS.add('/api/leaders/order')
POST_PATHS.add('/api/leaders/priority')
POST_PATHS.update(('/api/files/resolve', '/api/files/open', '/api/files/reveal'))
POST_PATHS.update(('/api/documents','/api/documents/pin','/api/documents/refresh'))
POST_PATHS.update(('/api/git/enable', '/api/git/disable', '/api/git/fetch', '/api/git/settings'))
POST_PATHS.update(('/api/admin/update/prepare', '/api/admin/update/cancel'))
POST_PATHS.add('/api/settings')
POST_PATHS.update(('/api/storage/preview', '/api/storage/clean', '/api/storage/restore',
                   '/api/storage/trash/empty', '/api/storage/policy'))
POST_PATHS.add('/api/member-defaults')
POST_PATHS.update(('/api/admin/chat/send', '/api/admin/chat/interrupt', '/api/admin/config'))
POST_PATHS.add('/api/admin/connection/prepare')
POST_PATHS.update(('/api/connections/configure', '/api/connections/action', '/api/connections/preferences'))
POST_PATHS.add('/api/native-approval')
POST_PATHS.add('/api/session-library/adopt')
POST_PATHS.update(('/api/maintenance/department/preview', '/api/maintenance/department/compact',
                   '/api/maintenance/cleanup/preview', '/api/maintenance/cleanup'))
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/themes.js": ("themes.js", "text/javascript; charset=utf-8"),
          "/sounds.js": ("sounds.js", "text/javascript; charset=utf-8"),
          "/channel.js": ("channel.js", "text/javascript; charset=utf-8"),
          "/git.js": ("git.js", "text/javascript; charset=utf-8"),
          "/git.css": ("git.css", "text/css; charset=utf-8"),
          "/git-settings.js": ("git-settings.js", "text/javascript; charset=utf-8"),
          "/git-settings.css": ("git-settings.css", "text/css; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8"), "/chat.js": ("chat.js", "text/javascript; charset=utf-8"),
          "/vendor/markdown-it.min.js": ("vendor/markdown-it.min.js", "text/javascript; charset=utf-8")}
STATIC.update({'/logo.svg': ('logo.svg', 'image/svg+xml'),
               '/runtime-switch-ui.js':('runtime-switch-ui.js','text/javascript; charset=utf-8'),
               '/runtime-switch-ui.css':('runtime-switch-ui.css','text/css; charset=utf-8'),
               '/leader-trust-ui.js':('leader-trust-ui.js','text/javascript; charset=utf-8'),
               '/leader-trust-ui.css':('leader-trust-ui.css','text/css; charset=utf-8'),
               '/documents.js': ('documents.js','text/javascript; charset=utf-8'),
               '/documents.css': ('documents.css','text/css; charset=utf-8'),
               '/fonts/HarmonyOS_Sans_SC_Regular.woff2': ('fonts/HarmonyOS_Sans_SC_Regular.woff2', 'font/woff2'),
               '/fonts/HarmonyOS_Sans_SC_Medium.woff2': ('fonts/HarmonyOS_Sans_SC_Medium.woff2', 'font/woff2')})
STATIC_CACHED = {'/logo.svg', '/fonts/HarmonyOS_Sans_SC_Regular.woff2', '/fonts/HarmonyOS_Sans_SC_Medium.woff2'}
STATIC.update({'/goal-ui.js': ('goal-ui.js', 'text/javascript; charset=utf-8'),
               '/goal-ui.css': ('goal-ui.css', 'text/css; charset=utf-8')})
STATIC.update({'/settings-ui.js': ('settings-ui.js', 'text/javascript; charset=utf-8'),
               '/storage-ui.js': ('storage-ui.js', 'text/javascript; charset=utf-8'),
               '/storage-ui.css': ('storage-ui.css', 'text/css; charset=utf-8'),
               '/connections-ui.js': ('connections-ui.js', 'text/javascript; charset=utf-8'),
               '/settings-ui.css': ('settings-ui.css', 'text/css; charset=utf-8')})
STATIC.update({'/operations-ui.js': ('operations-ui.js', 'text/javascript; charset=utf-8'),
               '/operations-ui.css': ('operations-ui.css', 'text/css; charset=utf-8')})
STATIC.update({'/member-ui.js': ('member-ui.js', 'text/javascript; charset=utf-8'), '/member-ui.css': ('member-ui.css', 'text/css; charset=utf-8')})
STATIC['/launch-settings-ui.js'] = ('launch-settings-ui.js', 'text/javascript; charset=utf-8')
STATIC.update({'/workspace-settings-ui.js': ('workspace-settings-ui.js', 'text/javascript; charset=utf-8'),
               '/workspace-settings-ui.css': ('workspace-settings-ui.css', 'text/css; charset=utf-8')})
STATIC.update({'/native-approval-ui.js': ('native-approval-ui.js', 'text/javascript; charset=utf-8'),
               '/native-approval-ui.css': ('native-approval-ui.css', 'text/css; charset=utf-8')})
STATIC.update({'/session-library.js': ('session-library.js', 'text/javascript; charset=utf-8'),
               '/session-library.css': ('session-library.css', 'text/css; charset=utf-8')})
STATIC.update({'/maintenance-ui.js': ('maintenance-ui.js', 'text/javascript; charset=utf-8'),
               '/maintenance-ui.css': ('maintenance-ui.css', 'text/css; charset=utf-8')})


class Handler(BaseHTTPRequestHandler):
    def _connection_access(self):
        port = self.server.server_port
        hosts = {'127.0.0.1:%d' % port, 'localhost:%d' % port, '[::1]:%d' % port}
        origins = self.headers.get_all('Origin')
        return (self.headers.get_all('Host') in ([host] for host in hosts)
                and (origins is None or len(origins) == 1 and origins[0] in {'http://' + host for host in hosts}))

    def _chat_events(self, journal, sequence):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            while not self.server.service.stop.is_set():
                events = journal.after(sequence, wait=True)
                for event in events:
                    self.wfile.write(("id: %s\ndata: %s\n\n" %
                                      (event["seq"], json.dumps(event, ensure_ascii=False))).encode("utf-8"))
                    sequence = event["seq"]
                if not events:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _send(self, code, content, content_type="application/json; charset=utf-8", attachment=None, cache_control='no-store'):
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", cache_control)
        if attachment is not None:
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % attachment)
        self.end_headers()
        self.wfile.write(content)

    def send_file_content(self, content, media_type, *, interactive_report=False):
        # SVG must remain an image even when the resource URL is opened directly.
        if isinstance(content, dict):
            content = json.dumps(content, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        if interactive_report:
            from .report_documents import VIEWER_POLICY
            policy = 'sandbox allow-scripts; ' + VIEWER_POLICY
        else:
            policy = "sandbox; default-src 'none'; style-src 'unsafe-inline'"
        self.send_header("Content-Security-Policy", policy)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def _dispatch(self, method):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        service = self.server.service
        local_settings_path = (url.path in ('/api/connections', '/api/connections/configure', '/api/connections/action', '/api/connections/preferences')
                               or url.path == '/api/storage' or url.path.startswith('/api/storage/'))
        if method == "POST":
            length = int(self.headers.get('Content-Length', '0'))
            payload = self.rfile.read(length) if length > 0 else b''
            if local_settings_path and not self._connection_access():
                self._send(403, {'error': '此设置只允许本机 Crew 页面访问'})
                return
            # Drain the request before an early error, so Windows can deliver
            # the HTTP response instead of resetting a socket with unread data.
            if self.headers.get("X-Crew") != "1":
                self._send(403, {"error": "\u7f3a\u5c11 X-Crew: 1 \u8bf7\u6c42\u5934"})
                return
            if url.path not in POST_PATHS:
                self._send(404, {"error": "\u63a5\u53e3\u4e0d\u5b58\u5728"})
                return
            if length <= 0:
                raise ValueError("\u8bf7\u63d0\u4f9b JSON \u8bf7\u6c42\u4f53")
            data = json.loads(payload.decode("utf-8"))
            self._send(200, service.post(url.path, data, query.get("project", [None])[0]))
        elif local_settings_path and not self._connection_access():
            self._send(403, {'error': '此设置只允许本机 Crew 页面访问'})
        elif url.path == "/api/files/content":
            hosts = {"127.0.0.1:%d" % self.server.server_port, "localhost:%d" % self.server.server_port}
            if self.headers.get_all("Host") not in ([host] for host in hosts):
                self._send(403, {"error": "\u6587\u4ef6\u8bfb\u53d6\u53ea\u5141\u8bb8\u672c\u673a Host"})
                return
            document_id=int(query['document_id'][0]) if 'document_id' in query else None
            info = service.resolve_file(query.get("session_id", [None])[0], query.get("path",[None])[0],
                                        query.get("project", [None])[0], query.get("base", [None])[0],document_id)
            content, media_type = service.read_file(info, raw=query.get("raw") == ["1"])
            self.send_file_content(content, media_type)
        elif url.path == '/api/admin/connection/plan':
            self._send(200, service.connection_plan())
        elif url.path == '/api/admin/chat':
            self._send(200, operations_chat.snapshot(service.home))
        elif url.path == '/api/admin/config':
            self._send(200, operations_settings.snapshot(service.home, service.plugin_root))
        elif url.path == '/api/admin/health':
            workers = {name: bool(worker and worker.is_alive()) for name, worker in (
                ('agents', service.worker), ('notices', service.notice_worker), ('git_ci', service.ci_worker))}
            self._send(200, {'ok': True, 'service': 'crew', 'home': str(Path(service.home).resolve()),
                             'pid': os.getpid(), 'workers': workers,
                             'warnings': [name for name, running in workers.items() if not running],
                             'version': service.version, 'plugin_root': str(service.plugin_root.resolve()),
                             'instance_id': service.instance_id, 'port': self.server.server_port,
                             'update_gate': service.chat.paused() is not None})
        elif url.path == '/api/workspaces/activity':
            with closing(store.connect(service.home)) as conn:
                results = []
                for name in dict.fromkeys(query.get('project', [])):
                    try:
                        results.append(workspace_activity.snapshot(service, conn, name))
                    except board.BoardError as exc:
                        results.append({'project': name, 'error': str(exc)})
                self._send(200, {'projects': results})
        elif url.path == "/api/state":
            self._send(200, service.state(query.get("project", [None])[0]))
        elif url.path in ('/api/session-library', '/api/session-library/preview'):
            with closing(store.connect(service.home)) as conn:
                project = service.selected_project(conn, query.get('project', [None])[0])
                scope = query.get('scope', ['project'])[0]
                result = (session_library.preview(service, conn, project, query.get('key', [''])[0], scope)
                          if url.path.endswith('/preview') else session_library.listing(service, conn, project, scope))
                self._send(200, result)
        elif url.path in ('/api/goals', '/api/goal', '/api/goals/report'):
            with closing(store.connect(service.home)) as conn:
                project = service.selected_project(conn, query.get('project', [None])[0])
                if url.path == '/api/goals':
                    self._send(200, {'goals': [goal_api.card(conn, goal) for goal in goals.list_goals(conn, project)]})
                elif url.path == '/api/goal':
                    self._send(200, goal_api.detail(conn, project, query.get('id', [''])[0]))
                else:
                    goal_id = query.get('goal_id', [''])[0]
                    goals.get(conn, goal_id, project)
                    report = goals.get_report(conn, query.get('report_id', [''])[0])
                    if report['goal_id'] != goal_id:
                        raise board.BoardError('报告不属于此目标')
                    self.send_file_content(report['html'].encode('utf-8'), 'text/html; charset=utf-8', interactive_report=True)
        elif url.path == '/api/runtimes':
            catalog = service.runtime_catalog.refresh(force=True) if query.get('refresh') == ['1'] else service.runtime_catalog.refresh()
            self._send(200, {'runtimes': catalog})
        elif url.path == '/api/connections':
            self._send(200, service.connections.snapshot())
        elif url.path == '/api/settings':
            self._send(200, service.settings_state())
        elif url.path == '/api/storage':
            self._send(200, service.storage_cleanup.snapshot())
        elif url.path == '/api/project/settings':
            self._send(200, service.workspace_state(query.get('project', [None])[0]))
        elif url.path == '/api/projects':
            with closing(store.connect(service.home)) as conn:
                self._send(200, {'projects': board.projects(conn, include_removed=True) if query.get('include_removed') == ['1'] else board.projects(conn)})
        elif url.path == '/api/documents':
            self._send(200,service.document_list(query.get('project',[None])[0],query.get('leader_session_id',[None])[0],
                                                 query.get('goal_id',[None])[0]))
        elif url.path == '/api/trust/report':
            with closing(store.connect(service.home)) as conn:
                project = service.selected_project(conn, query.get('project', [None])[0])
                self._send(200, trust_mode.report(conn, project,_text({'leader_session_id':query.get('leader_session_id',[None])[0]},'leader_session_id')).encode('utf-8'),
                           'text/markdown; charset=utf-8', attachment='crew-report.md')
        elif url.path == '/api/comments':
            with closing(store.connect(service.home)) as conn:
                project = service.selected_project(conn, query.get('project', [None])[0])
                sid = query.get('session_id', [None])[0]
                if sid is not None and board.session(conn, sid)['project'] != project:
                    raise board.BoardError('评论对象不属于当前工作区')
                rows = board._rows(conn, 'SELECT * FROM workspace_comments WHERE project=?' +
                                   (' AND session_id=?' if sid else '') + ' ORDER BY id', (project, sid) if sid else (project,))
                self._send(200, {'comments': rows})
        elif url.path in ('/api/maintenance/jobs', '/api/maintenance/archive'):
            with closing(store.connect(service.home)) as conn:
                project = service.selected_project(conn, query.get('project', [None])[0])
                if url.path.endswith('/jobs'):
                    kind = query.get('kind', [''])[0]
                    self._send(200, {'jobs': maintenance.jobs(conn, project, kind, query.get('leader_session_id', [None])[0]),
                                     'cleanups': board.cleanup_boundary(conn, project)['cleanups']})
                else:
                    before = int(query['before'][0]) if 'before' in query else None
                    self._send(200, maintenance.archive(conn, project, query.get('job_id', [''])[0], query.get('kind', [''])[0],
                                                        before=before))
        elif url.path == '/api/todos':
            with closing(store.connect(service.home)) as conn:
                project = service.selected_project(conn, query.get('project', [None])[0])
                self._send(200, {'todos': todo.list(conn, project)})
        elif url.path == '/api/channel':
            before = int(query['before'][0]) if 'before' in query else None
            self._send(200, service.channel_messages(query.get('project', [None])[0],
                                                    int(query.get('limit', [20])[0]), before))
        elif url.path == "/api/git":
            self._send(200, service.git(query.get("project", [None])[0], int(query.get("offset", [0])[0])))
        elif url.path == '/api/git/settings':
            self._send(200, service.git_settings(query.get('project', [None])[0]))
        elif url.path == "/api/git/commit":
            self._send(200, service.git_commit(query.get("project", [None])[0], query.get("sha", [""])[0]))
        elif url.path == '/api/chat/file':
            content, media_type = service.chat_file(query.get('session_id', [''])[0], query.get('name', [''])[0],
                                                    query.get('project', [None])[0], query.get('goal_id', [None])[0])
            self._send(200, content, media_type)
        elif url.path in ("/api/chat/history", "/api/chat/events"):
            session_id = query.get("session_id", [""])[0]
            history = service.chat_history(session_id, query.get("project", [None])[0], query.get('goal_id', [None])[0])
            if url.path.endswith("/history"):
                self._send(200, history)
            else:
                sequence = max(int(query.get("after", [0])[0]), int(self.headers.get("Last-Event-ID", 0)))
                # Empty scoped conversations can be read without launching a model.
                key = history.get('execution_id') or ('goal-' + query['goal_id'][0] + '-' + session_id if query.get('goal_id') else session_id)
                self._chat_events(service.chat.journal(key), sequence)
        elif url.path in ("/api/usage", "/api/usage/export"):
            period = query.get("period", ["week"])[0]
            if url.path.endswith("/export"):
                what = query.get("what", [None])[0]
                if what not in usage_cli.EXPORT_FIELDS:
                    raise ValueError("\u5bfc\u51fa\u7c7b\u578b\u5fc5\u987b\u662f sessions / daily / codex / requests")
                rows = service.usage(period, what, query.get('project', [None])[0], query.get('scope', ['workspace'])[0])
                stream = io.StringIO(newline="")
                writer = csv.DictWriter(stream, fieldnames=usage_cli.EXPORT_FIELDS[what])
                writer.writeheader()
                writer.writerows({key: "" if value is None else usage_cli._format(value, key) for key, value in row.items()} for row in rows)
                self._send(200, stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", "crew-" + what + ".csv")
            else:
                self._send(200, service.usage(period, project_name=query.get('project', [None])[0], scope=query.get('scope', ['workspace'])[0]))
        elif url.path in STATIC:
            filename, content_type = STATIC[url.path]
            self._send(200, (service.plugin_root / "crew" / "web" / filename).read_bytes(), content_type,
                       cache_control='public, max-age=604800' if url.path in STATIC_CACHED else 'no-store')
        else:
            self._send(404, {"error": "\u9875\u9762\u4e0d\u5b58\u5728"})

    def _handle(self, method):
        try:
            self._dispatch(method)
        except board.Conflict as exc:
            self._send(409, {"error": str(exc)})
        except (board.BoardError, ValueError) as exc:
            self._send(400, {"error": str(exc)})
        except Exception:
            # The HTTP boundary must return 500 and print the original traceback.
            traceback.print_exc()
            self._send(500, {"error": "\u670d\u52a1\u5668\u9519\u8bef\uff0c\u8bf7\u67e5\u770b\u670d\u52a1\u7aef\u65e5\u5fd7"})

    def log_message(self, format, *args):
        # The page polls every 3 seconds; access lines would bury real errors (tracebacks still print).
        return

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, service, port=47811):
        super().__init__(("127.0.0.1", port), Handler)
        self.service = service
        self.url = "http://127.0.0.1:%d" % self.server_port
        (service.home / "web.json").write_text(json.dumps({"url": self.url}), encoding="utf-8")
        service.start()

    def server_close(self):
        self.service.close()
        address = self.service.home / "web.json"
        if address.exists() and json.loads(address.read_text(encoding="utf-8"))["url"] == self.url:
            address.unlink()
        super().server_close()


def create_server(home, plugin_root, port=47811, **options):
    return Server(Service(home, plugin_root, **options), port)


def project_url(port, project_name=None):
    url = 'http://127.0.0.1:%d/' % port
    return url + ('?' + urlencode({'project': project_name}) if project_name is not None else '')


def serve(home, plugin_root, port=47811, open_browser=False, **options):
    # CLI/autostart owns the same lifetime lock as crew_desktop. Acquire before
    # store.connect (which can migrate the DB) or starting any service workers.
    lock = HomeServiceLock(home)
    if not lock.acquire():
        if open_browser:
            return _open_registered_service(home, options.get('cwd') or Path.cwd())
        raise board.BoardError("CREW_BACKEND_IN_USE: 此 Crew 工作区已有后台在运行，请打开现有 Crew 应用。")
    try:
        return _serve_locked(home, plugin_root, port, open_browser, **options)
    finally:
        lock.release()


def _open_registered_service(home, cwd):
    """Open a contended HOME using HTTP only; never initialize local storage."""
    from . import autostart
    try:
        # A lock can be acquired before a starting backend publishes web.json.
        # Missing registration must not fall back to the unrelated default port.
        if not (Path(home) / 'web.json').is_file():
            raise ValueError('服务尚未登记地址')
        url = autostart.service_url(home)
        opener = build_opener(ProxyHandler({}), autostart._NoRedirect())
        def read(route):
            with opener.open(url + route, timeout=3) as response:
                return json.load(response)
        state = read('/api/state')
        if not isinstance(state, dict) or not all(key in state for key in ('projects', 'leaders', 'candidates')):
            raise ValueError('登记地址不是可识别的 Crew 服务')
        try:
            projects = read('/api/projects')['projects']
        except HTTPError as exc:
            if exc.code != 404:
                raise
            projects = state['projects']
        if not isinstance(projects, list):
            raise ValueError('项目列表格式无效')
        matches = []
        for project in projects:
            name = project['name']
            if not isinstance(name, str) or not name:
                raise ValueError('项目名称无效')
            if not project.get('root'):
                project = read('/api/state?' + urlencode({'project': name}))['project']
                if project['name'] != name:
                    raise ValueError('登记项目与服务响应不一致')
            root = project['root']
            if not isinstance(root, str) or not root:
                raise ValueError('项目目录无效')
            if store.is_under(cwd, root):
                matches.append((len(store.norm_path(root)), name))
        selected = max(matches)[1] if matches else None
        target = url + '/' + ('?' + urlencode({'project': selected}) if selected is not None else '')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise board.BoardError('CREW_BACKEND_IN_USE: 已有后台，但暂时无法确认其登记地址；不重复启动。' + str(exc)) from exc
    if not webbrowser.open(target):
        raise board.BoardError('已有 Crew 后台，请手动打开：' + target)
    print(target, flush=True)


def _serve_locked(home, plugin_root, port, open_browser, **options):
    options.setdefault('migrate_manual_work', True)
    url = project_url(port)
    with closing(store.connect(home)) as conn:
        project = board.project_for_path(conn, options.get('cwd') or Path.cwd())
    workspace_url = project_url(port, project['name'] if project else None)
    if open_browser:
        try:
            with build_opener(ProxyHandler({})).open(url + 'api/state', timeout=3) as response:
                state = json.load(response)
        except URLError as exc:
            if not isinstance(exc.reason, ConnectionRefusedError):
                raise
        else:
            if not all(key in state for key in ('projects', 'leaders', 'members')):
                raise board.BoardError('端口已被其他服务占用')
            print(workspace_url, flush=True)
            webbrowser.open(workspace_url)
            return
    with create_server(home, plugin_root, port, **options) as server:
        url = project_url(server.server_port)
        workspace_url = project_url(server.server_port, project['name'] if project else None)
        print(url, flush=True)
        if open_browser:
            webbrowser.open(workspace_url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
