"""Loopback-only board HTTP service with injectable external sources."""
import datetime as dt
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
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import ProxyHandler, build_opener
from urllib.error import HTTPError, URLError

from . import board, channel, channel_digest, chat, claude_limits, codex_runtime, documents, git_admin, git_ci, git_view, goals, hooks, maintenance, messaging, notifications, runtime_catalog, runtime_switch, runtimes, session_library, session_ops, settings, store, trust_mode, updater, usage, vscode_sessions, workspace_execution, workspace_settings
from .service_lock import HomeServiceLock
from . import operations_chat, tasks
from . import remote_access
from . import connections, session_presence, storage_cleanup
from .storage_service import StorageService, HISTORY_PREFIX
from . import approval_actions, notification_ports, notice_delivery, workspace_activity, workspace_snapshot
from .web_api.database import text as _text
from .web_assets import AssetCatalog
from .task_notifications import TaskNotifications
from .web_api import build as build_api, Request as ApiRequest, Content as ApiContent, Download as ApiDownload, Events as ApiEvents, NotFound as ApiNotFound, Response as ApiResponse

MAX_REQUEST_BYTES = 90 * 1024 * 1024


class Service:
    def __init__(self, home, plugin_root, agents_provider=None,
                 claude_root=None, codex_root=None, runs_file=None, now=None, cwd=None, process_factory=subprocess.Popen,
                 notification_client=None, lock_status=notifications.desktop_locked, notification_batch_seconds=30,
                 vscode_state_db=None, isolated_sources=False):
        if agents_provider is None:
            from .cli import claude_agents
            agents_provider = claude_agents
        self.home, self.plugin_root = Path(home), Path(plugin_root)
        self.remote_access = remote_access.Access(self.home)
        self.storage = StorageService(self.home, isolated=isolated_sources)
        self.storage.repair_pending()
        self.agents_provider, self.clock = agents_provider, now
        # Health reports which bundled source this process really runs.
        self.instance_id = uuid.uuid4().hex
        self.version = updater.manifest_version(self.plugin_root)
        self.update_drain_seconds = 10
        self.chat = chat.Manager(home, plugin_root, agents_provider, process_factory)
        self.claude_root = Path.home() / ".claude" / "projects" if claude_root is None else Path(claude_root)
        self.codex_root = Path.home() / ".codex" / "sessions" if codex_root is None else Path(codex_root)
        self.runtime_catalog = runtime_catalog.Catalog(home=self.home, claude_config_root=self.claude_root.parent,
            codex_config_root=self.codex_root.parent, loaded_provider=self.chat.executions.runtime_observations)
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
        self.connections = connections.Manager(self.home, self.write_lock, self.remote_workspaces(), self.remote_conversations(),
                                               notification_client, lock_status, notification_batch_seconds)
        origins = self.remote_access.public()['origins']
        self.task_notifications = TaskNotifications(self.home, self.connections, base_url=origins[0] if origins else None)
        # Marks maintenance left unfinished by a previous service as interrupted; nothing is re-sent.
        self.maintenance = maintenance.Manager(self.home, self.chat, self.write_lock, self.channel_digest.clear_failures)
        self.storage_cleanup = storage_cleanup.Manager(self.home, self.plugin_root, clock=self.now,
                                                      active_keys=self.storage_active_keys, activity_lock=self.chat.lock)
        with closing(store.connect(home)) as conn:
            projects = board.projects(conn)
            project = board.project_for_path(conn, Path.cwd() if cwd is None else cwd)
            self.default_project = project["name"] if project is not None else None
        self.assets = AssetCatalog(self.plugin_root / 'crew' / 'web')
        self.api = build_api(self)

    @property
    def inbox(self):
        return self.connections.inboxes['qq']

    def remote_workspaces(self):
        return notification_ports.WorkspaceQueries(self.now, lambda name: self.state(name)['attention'],
                                                   self.observe_agents, self.managed_agents)

    def remote_conversations(self):
        return notification_ports.Conversations(self.chat.executions, self.chat.work, self.chat.send, self.chat.is_owned)

    def health_snapshot(self, port):
        workers = {name: bool(worker and worker.is_alive()) for name, worker in (
            ('agents', self.worker), ('notices', self.notice_worker), ('git_ci', self.ci_worker))}
        return {'ok': True, 'service': 'crew', 'home': str(self.home.resolve()),
                'pid': os.getpid(), 'workers': workers,
                'warnings': [name for name, running in workers.items() if not running],
                'version': self.version, 'plugin_root': str(self.plugin_root.resolve()),
                'instance_id': self.instance_id, 'port': port,
                'update_gate': self.chat.paused() is not None}

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
            try:
                self.task_notifications.tick()
            except (board.BoardError, sqlite3.Error, OSError):
                traceback.print_exc()
            with self.chat.work(background=True) as admitted:
                if not admitted:
                    continue
                try:
                    with self.chat.lock, closing(store.connect(self.home)) as conn:
                        self.chat.goal_controller.sync(conn)
                        tasks.finalize_lifecycle_intents(conn)
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
            notice_delivery.Delivery(self.chat.executions, self.chat.send, self.chat.wake, self.observe_agents, self.now).deliver(conn)

    def deliver_git_results(self, conn):
        return notice_delivery.Delivery(self.chat.executions, self.chat.send, self.chat.wake, self.observe_agents, self.now).git_results(conn)

    def selected_project(self, conn, name):
        name = self.default_project if name is None else name
        if name is None:
            raise board.BoardError("\u8bf7\u5148 crew init \u542f\u7528\u9879\u76ee")
        return board.project(conn, name)["name"]

    def library_sources(self, projects):
        with self.agents_lock:
            return session_library.Discovery(
                board.live_sessions(self.agents) if self.agents_error is None else {},
                {name: list(self.vscode_sessions.get(name, [])) for name in projects},
                self.vscode_hidden_ids, self.vscode_error)

    def library(self):
        return session_library.Library(self.claude_root, self.codex_root, self.chat.executions,
                                       self.library_sources, self.adopt, self._adopt)

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

    def observe_agents(self):
        with self.agents_lock:
            return session_presence.Observation(tuple(self.agents), self.agents_error, self.agents_ready.is_set())

    def title_contexts(self, sessions):
        with self.scan_lock:
            titles = usage.session_titles(self.claude_root, [session['session_id'] for session in sessions], self.title_cache)
            return titles, {sid: cached['context'] for sid, cached in self.title_cache.items()}

    def state(self, project_name=None):
        self.scan_usage(live_dispatch=True)
        reader = workspace_snapshot.Snapshot(self.home, self.chat.executions, self.chat.goal_controller.active_executions,
            lambda conn, session, agents, error: runtime_switch.state(conn, session, self.chat, agents, error),
            self.now, self.selected_project,
            self.runtime_catalog.snapshot, self.notification_status, self.limits, self.native_approvals,
            self.candidates, self.title_contexts, self.managed_agents, self.channel_digest.state)
        return reader.read(project_name, self.observe_agents(), vscode_error=self.vscode_error)

    def activity(self):
        return workspace_activity.Activity(self.chat.executions, self.chat.goal_controller.display_execution,
            self.managed_agents, self.observe_agents, self.native_approvals, self.now)

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

    def change_workspace(self, data, project_name, *, remove=False):
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
            if remove:
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


    def native_approvals(self, project):
        return approval_actions.Approvals(self.chat.executions).listing(project)


    def post(self, path, data, project_name=None, *, access=None):
        query = {} if project_name is None else {'project': [project_name]}
        return self.api.dispatch('POST', ApiRequest(path, query, data, access=access))


    def archive_session(self, conn, session_id, removed=False, now=None):
        result = session_ops.archive(conn, session_id, removed=removed, now=now)
        self.chat.stop_managed(session_id)
        return result

    def stop_archived(self, before, with_members=True):
        for entry in before['sessions']:
            if with_members or entry['session_id'] == before['group']['leader_session_id']:
                self.chat.stop_managed(entry['session_id'])



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


    def scan_usage(self, *, live_dispatch=False):
        with self.write_lock, self.scan_lock, closing(store.connect(self.home)) as conn:
            now = self.now()
            full = self.scanned_at is None or (now - self.scanned_at).total_seconds() >= 60
            if full or live_dispatch:
                usage.scan(conn, self.claude_root, self.codex_root, self.runs_file, receipts_only=not full)
            if full:
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
                try:
                    self.server.service.remote_access.authorize(self.headers, self.server.server_port,
                        path='/api/chat/events', method='GET')
                except remote_access.AccessError:
                    break
                events = journal.after(sequence, wait=True)
                try:
                    self.server.service.remote_access.authorize(self.headers, self.server.server_port,
                        path='/api/chat/events', method='GET')
                except remote_access.AccessError:
                    break
                for event in events:
                    self.wfile.write(("id: %s\ndata: %s\n\n" %
                                      (event["seq"], json.dumps(event, ensure_ascii=False))).encode("utf-8"))
                    sequence = event["seq"]
                if not events:
                    self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _send(self, code, content, content_type="application/json; charset=utf-8", attachment=None, cache_control='no-store', headers=()):
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", cache_control)
        for name, value in headers:
            self.send_header(name, value)
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

    def _stream_storage_file(self, path, media_type, *, attachment=None, sandbox=False):
        if not isinstance(media_type,str) or any(c in media_type for c in '\r\n\x00'):
            media_type='application/octet-stream'
        with Path(path).open('rb') as source:
            self.send_response(200)
            self.send_header('Content-Type', media_type)
            self.send_header('Content-Length', str(os.fstat(source.fileno()).st_size))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            if sandbox:
                self.send_header('Content-Security-Policy', "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:")
            if attachment:
                self.send_header('Content-Disposition', 'attachment; filename="'+attachment+'"')
            self.end_headers()
            while chunk := source.read(1024 * 1024):
                self.wfile.write(chunk)

    def _respond(self, result):
        if isinstance(result, ApiResponse):
            self._send(result.status, result.body, headers=result.headers)
        elif isinstance(result, ApiContent):
            if result.sandbox or result.interactive_report:
                self.send_file_content(result.body, result.media_type, interactive_report=result.interactive_report)
            else:
                self._send(200, result.body, result.media_type, result.attachment)
        elif isinstance(result, ApiDownload):
            self._stream_storage_file(result.path, result.media_type,
                                      attachment=result.attachment, sandbox=result.sandbox)
        elif isinstance(result, ApiEvents):
            self._chat_events(result.journal, result.after)
        else:
            self._send(200, result)

    def _dispatch(self, method):
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        service = self.server.service
        route = service.api.lookup(method, url.path)
        local_only = service.api.local_only(url.path)
        if method == 'POST':
            length = int(self.headers.get('Content-Length', '0'))
            if length > MAX_REQUEST_BYTES:
                self.close_connection = True
                self._send(413, {'error': '请求正文超过 90 MiB 上限'})
                return
            # Drain before an early error; otherwise Windows can reset the socket.
            payload = self.rfile.read(length) if length > 0 else b''
            access = service.remote_access.authorize(self.headers, self.server.server_port, path=url.path, method=method)
            if local_only and not (access['authenticated'] and not access['local'] or self._connection_access()):
                self._send(403, {'error': '此设置只允许本机 Crew 页面访问'})
                return
            if self.headers.get('X-Crew') != '1':
                self._send(403, {'error': '缺少 X-Crew: 1 请求头'})
                return
            if route is None:
                self._send(404, {'error': '接口不存在'})
                return
            if length <= 0:
                raise ValueError('请提供 JSON 请求体')
            self._respond(service.post(url.path, json.loads(payload.decode('utf-8')),
                query.get('project', [None])[0], access=access))
            return
        access = service.remote_access.authorize(self.headers, self.server.server_port, path=url.path, method=method)
        if local_only and not (access['authenticated'] and not access['local'] or self._connection_access()):
            self._send(403, {'error': '此设置只允许本机 Crew 页面访问'})
            return
        if route is not None:
            if route.host_only:
                hosts = {'127.0.0.1:%d' % self.server.server_port, 'localhost:%d' % self.server.server_port}
                if access['local'] and self.headers.get_all('Host') not in ([host] for host in hosts):
                    self._send(403, {'error': '文件读取只允许本机 Host'})
                    return
            request = ApiRequest(url.path, query, last_event_id=self.headers.get('Last-Event-ID', 0),
                                 port=self.server.server_port, access=access)
            self._respond(service.api.dispatch('GET', request))
            return
        asset = service.assets.resolve(url.path, history=query.get('project', [''])[0].startswith(HISTORY_PREFIX))
        if asset is not None:
            self._send(200, asset.read_bytes(), asset.content_type, cache_control=asset.cache_control)
        else:
            self._send(404, {'error': '页面不存在'})

    def _handle(self, method):
        try:
            self._dispatch(method)
        except ApiNotFound as exc:
            self._send(404, {'error': str(exc)})
        except remote_access.AccessError as exc:
            self._send(exc.status, {'error': str(exc), 'code': 'unauthorized' if exc.status == 401 else 'forbidden'})
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
