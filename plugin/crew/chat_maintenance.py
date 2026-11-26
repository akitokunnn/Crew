from contextlib import closing
import json

from . import board, store, workspace_execution
from .chat_base import MAINTENANCE_HELD, _admitted


class MaintenanceMixin:
    @_admitted
    def compact(self, session, caller_session_id=None, after_turn=False):
        logical_id = session['session_id']
        execution = session.get('_execution')
        session_id = session.get('_storage_id', logical_id)
        self_compact = caller_session_id not in (None, board.HUMAN) and caller_session_id == logical_id
        deferred = self_compact or after_turn and caller_session_id in (None, board.HUMAN)
        with self.lock:
            if caller_session_id not in (None, board.HUMAN):
                with closing(store.connect(self.home)) as conn:
                    leader = conn.execute(
                        "SELECT s.* FROM groups g JOIN sessions s ON s.session_id=g.leader_session_id "
                        "WHERE g.project=? AND g.name=? AND g.closed_at IS NULL AND s.session_id=? "
                        "AND s.role='leader' AND s.status!='closed' AND s.project=g.project",
                        (session["project"], session["group_name"], caller_session_id)).fetchone()
                    if leader is None or board.is_archived(conn, caller_session_id):
                        raise board.BoardError("只有它自己的 leader 或用户能压缩它")
                if self_compact and not self.running(session_id):
                    raise board.Conflict("它在 VS Code 里运行，去那边输入 /compact")
            running = self.running(session_id)
            if running:
                if self.busy(session_id) and not deferred:
                    raise board.Conflict("正在干活，等这一轮结束再压缩")
            elif (execution is None and self._runs_in_claude(logical_id)
                    and any(agent["sessionId"] == logical_id for agent in self._external_agents())):
                raise board.Conflict("它在 VS Code 里运行，去那边输入 /compact")
            if execution is not None:
                binding = next((b for b in execution['bindings'] if b['runtime'] == execution['runtime']), None)
                if (binding and execution['runtime'] == 'claude' and not running
                        and any(agent['sessionId'] == binding['native_id'] for agent in self._external_agents())):
                    raise board.Conflict('此目标在原工具中运行，请在那边输入 /compact')
                if not binding or not binding['native_id'] or not self.path(session_id).exists():
                    raise board.BoardError('此目标尚无原生执行历史，不能创建新会话来压缩')
            elif (not self.path(session_id).exists() or session["spawn_id"] is None or
                    not (self.home / "spawn" / (session["spawn_id"] + ".json")).exists()):
                raise board.BoardError("会话没有网页聊天记录或启动记录，请先叫醒它，或在 VS Code 打开后输入 /compact")
            if deferred and (self_compact or running):
                current = self.processes[session_id]
                with current.input_lock:
                    if current.process.poll() is not None:
                        raise board.Conflict("它在 VS Code 里运行，去那边输入 /compact")
                    if self.closed:
                        raise board.BoardError("网页服务正在关闭")
                    if self.maintenance_held(logical_id):
                        raise board.Conflict(MAINTENANCE_HELD)
                    if (current.journal.progress or {}).get('phase') == 'compacting':
                        return {"ok": True, "message": "正在压缩上下文"}
                    if current.journal.status == "working":
                        current.pending_compact = True
                        return {"ok": True, "message": "这一轮说完后压缩", **({'queued': True} if after_turn else {})}
                    return {"ok": True, "message_id": self._send(session_id, "/compact")}
            return self.send(session, "/compact")

    def maintenance_launch(self, session_id, execution, marker):
        """Resume one existing native history solely for compaction.

        The caller has confirmed this person has no other working native.
        Other idle conversations stay resident; business input for this person
        is held until the fixed maintenance command finishes.
        Unlike _launch this never claims the identity (nothing else is paused or
        stopped), never creates a spawn record or native binding, and never
        consults goal dispatch: a finished or paused goal keeps its state.
        """
        with self.lock:
            with closing(store.connect(self.home)) as conn:
                target = self.goal_controller.target(conn, execution) if execution is not None else board.session(conn, session_id)
                target = workspace_execution.execution_session(conn, target)
                if target['spawn_id'] is None or not (self.home / 'spawn' / (target['spawn_id'] + '.json')).is_file():
                    raise board.BoardError('没有启动记录，不能恢复原生会话')
                missing = self.missing_native(conn, target)
                if missing is not None:
                    raise board.BoardError('原会话 %s 已丢失，新会话尚未开始，没有可压缩的原生历史' % missing)
                spec = {**json.loads((self.home / 'spawn' / (target['spawn_id'] + '.json')).read_text(encoding='utf-8')),
                        'cwd': target['cwd'], 'project': target['project'],
                        '_workspace_notice': workspace_execution.migration_notice(conn, target)}
            self._launch_native(target, spec, resume=True, maintenance=marker)
            return self.processes[execution['id'] if execution is not None else session_id]

    def maintenance_compact(self, key, identity):
        """Submit only the native compact command to a maintenance-held process.

        Returns the journal position before the request; completion is observed
        from later native events, never from this submission.
        """
        with self.lock:
            current = self.processes.get(key)
            if getattr(current, 'maintenance', None) is None or not self.running(key):
                raise board.Conflict('维护压缩没有持有运行中的原生进程')
            journal = current.journal
            with current.input_lock:
                with journal.condition:
                    journal.refresh()
                    start = journal.events[-1]['seq'] if journal.events else 0
                journal.message(identity, '/compact', source='crew')
                if getattr(current, 'runtime', 'claude') == 'codex':
                    current.compact()
                    return start
                journal.set_status('working')
                current.process.stdin.write(json.dumps({
                    'type': 'user', 'uuid': identity, 'session_id': current.native_id if current.execution else key,
                    'parent_tool_use_id': None, 'message': {'role': 'user', 'content': '/compact'}}) + '\n')
                current.process.stdin.flush()
            return start

    def maintenance_release(self, key, current):
        """Stop a native that maintenance started; a pre-existing process is only unmarked."""
        with self.lock:
            if current.maintenance['owned'] and self.processes.get(key) is current:
                # The marker stays if stopping fails, so nothing else can feed it.
                self.stop_managed(key)
                del self.processes[key]
            current.maintenance = None
