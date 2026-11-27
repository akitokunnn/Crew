import os
import subprocess
import sys
import time
import uuid
from contextlib import closing

from .. import board, codex_runtime, goals, runtimes, store, updater, workspace_execution
from .. import operations_chat
from ..web_api.common import _text


class UpdatesMixin:
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
        from ..operations import read_operations
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
        from ..operations import read_operations
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
