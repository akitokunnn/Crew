import json
import sqlite3
import subprocess
import threading
import traceback
from contextlib import closing

from .. import board, messaging, runtimes, store, vscode_sessions


class WorkersMixin:
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
                # Journal times are wall-clock, so idle time is measured on the same clock.
                self.chat.reclaim_idle()
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
        self.remote_access.start_saved()

    def close(self):
        self.stop.set()
        with self.chat.lock:
            self.chat.closed = True
        # Cancel independent digest natives before joining other pollers.
        actions = [self.report_previews.close, self.channel_digest.stop, self.remote_access.shutdown, self.storage_cleanup.close, self.connections.close]
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

    def _poll_git_ci(self):
        while not self.stop.is_set():
            self.git_ci.poll(now=self.now().isoformat())
            if self.stop.wait(300):
                break

    def deliver_notices(self):
        with self.write_lock, closing(store.connect(self.home)) as conn:
            self.deliver_git_results(conn)
            now = self.now().isoformat()
            self.chat.settle_channel_mentions(conn, now)
            with self.agents_lock:
                live = (None if self.agents_error is not None or not self.agents_ready.is_set()
                        else frozenset(agent['sessionId'] for agent in self.agents))
            self.chat.deliver_leader_notices(conn, live, now)

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
                    key = sid
                    if messaging.stopped(conn, sid):
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
