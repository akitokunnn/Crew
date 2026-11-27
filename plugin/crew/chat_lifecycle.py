from contextlib import closing
import datetime
import subprocess
from concurrent.futures import ThreadPoolExecutor

from . import runtime_recovery
from . import board, codex_client, goals, runtimes, store
from .chat_base import idle_reclaim_seconds


class LifecycleMixin:
    def _idle_for(self, current, now):
        """Seconds since this native last did anything, or None while it must stay resident."""
        if (current.process.poll() is not None or getattr(current, 'maintenance', None) is not None
                or current.pending_compact or getattr(current, 'pending_native_accept', None) is not None
                or getattr(current, 'replacing', None) is not None or getattr(current, 'native_lost', None) is not None
                or getattr(current, 'background_tasks', None)):
            return None
        journal = current.journal
        with journal.condition:
            journal.refresh()
            if journal.status != 'idle' or not journal.events:
                return None
            if any(task.get('status') == 'running' for task in journal.subagents.values()):
                return None
            last = journal.events[-1]['at']
        return (now - store.parse_iso(last)).total_seconds()

    def reclaim_idle(self, now=None, idle_seconds=None):
        """Close owned natives idle past the limit; the next input resumes the same native ID."""
        limit = idle_reclaim_seconds() if idle_seconds is None else idle_seconds
        if not limit:
            return []
        now = store.parse_iso(store.now_iso()) if now is None else now
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.timezone.utc)
        reclaimed = []
        with self.lock:
            if self.closed:
                return []
            for key, current in list(self.processes.items()):
                with current.input_lock:
                    idle = self._idle_for(current, now)
                    if idle is None or idle < limit:
                        continue
                    current.stop_reason = 'idle_reclaim'
                    if getattr(current, 'runtime', 'claude') != 'codex':
                        try:
                            current.process.stdin.close()
                        except OSError as exc:
                            current.stop_reason = None
                            current.journal.emit('error', text='空闲回收关闭输入失败，进程保留：' + str(exc))
                            continue
                try:
                    if getattr(current, 'runtime', 'claude') == 'codex':
                        current.close()
                    else:
                        try:
                            current.process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            current.process.terminate()
                            current.process.wait(timeout=10)
                except (OSError, subprocess.SubprocessError, codex_client.CodexError) as exc:
                    # The shared poller must survive one stuck native; the next poll retries it.
                    current.journal.emit('error', text='空闲回收未能结束进程：%s: %s' % (type(exc).__name__, exc))
                    continue
                if not current.reader_done.wait(10):
                    current.journal.emit('error', text='空闲回收后进程输出尚未排空')
                current.journal.emit('runtime_released', reason='idle', idle_seconds=int(idle))
                reclaimed.append(key)
        return reclaimed

    def stop_managed(self, session_id, *, reason='managed_stop'):
        """Terminate an owned process, including busy turns, and drain its output."""
        with self.lock:
            current = self.processes.get(session_id)
            if current is None:
                return
            if getattr(current, "runtime", "claude") == "codex":
                with current.input_lock:
                    if current.process.poll() is None:
                        current.stop_reason = reason
                current.close()
                if not current.reader_done.wait(10):
                    raise board.BoardError("Codex 进程已关闭，但输出尚未排空")
                return
            with current.input_lock:
                current.pending_compact = False
                if current.process.poll() is None:
                    current.stop_reason = reason
                    try:
                        current.process.terminate()
                    except OSError:
                        current.stop_reason = None
                        raise
            try:
                current.process.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                raise board.BoardError('托管会话终止后仍未退出') from exc
            if not current.reader_done.wait(10):
                raise board.BoardError('托管会话进程已退出，但输出尚未读完')
            current.stderr_reader.join(timeout=10)
            if current.stderr_reader.is_alive():
                raise board.BoardError('托管会话进程已退出，但错误输出尚未读完')

    def stop_identity(self, session_id, caller_session_id=None):
        """Persist a stop gate, then confirm every owned native for this person has exited."""
        actor = board.HUMAN if caller_session_id is None else caller_session_id
        with self.lock, closing(store.connect(self.home)) as conn:
            target = board.session(conn, session_id)
            if actor != board.HUMAN:
                caller = board.session(conn, actor)
                leader = board.leader_of(conn, session_id)
                if caller['project'] != target['project'] or (actor != session_id and
                        (leader is None or leader['session_id'] != actor or caller['role'] != 'leader')):
                    raise board.BoardError('只能停止自己或自己组内的成员')
            conn.execute('INSERT INTO session_stop_gates(session_id,stopped_at,stopped_by) VALUES(?,?,?) '
                         'ON CONFLICT(session_id) DO UPDATE SET stopped_at=excluded.stopped_at,stopped_by=excluded.stopped_by',
                         (session_id, store.now_iso(), actor))
            keys = self._identity_keys(conn, session_id)
            owned_claude = {key: getattr(self.processes[key], 'native_id', key) for key in keys
                            if self.running(key) and getattr(self.processes[key], 'runtime', 'claude') == 'claude'}
            paused = set()
            if target['role'] == 'leader':
                for row in conn.execute("SELECT id FROM goals WHERE leader_session_id=? AND status NOT IN ('completed','failed','cancelled')",
                                        (session_id,)):
                    goals.request_pause(conn, row['id'], manual=True, actor=actor, reason='明确停止此人员')
                    paused.add(row['id'])
            for key in keys:
                if self.running(key) and self.busy(key):
                    try:
                        self.interrupt(key)
                    except (board.BoardError, codex_client.CodexError, OSError) as exc:
                        self.journal(key).emit('error', text='原生中断未确认，继续关闭托管进程：' + str(exc))
                self.stop_managed(key, reason='user_stop')
                if self.auto_candidates is not None:
                    self.auto_candidates.pop(key, None)
            external_claude = set()
            for key in keys:
                native_claude = set()
                if key == session_id:
                    mapping = runtimes.get(conn, key)
                    bindings = list(conn.execute('SELECT runtime,native_id FROM session_runtime_bindings WHERE session_id=?', (key,)))
                    if mapping['runtime'] == 'claude':
                        native_claude.add(mapping['native_id'] or key)
                else:
                    execution = goals.get_execution(conn, key)
                    mapping, bindings = execution, execution['bindings']
                native_claude.update(binding['native_id'] for binding in bindings
                                     if binding['runtime'] == 'claude' and binding['native_id'])
                external_claude.update(native_claude - {owned_claude.get(key)})
                if mapping['runtime'] == 'codex' or any(binding['runtime'] == 'codex' for binding in bindings):
                    from .codex_runtime import RuntimeLease
                    try:
                        lease = RuntimeLease(self.home, key).acquire()
                        lease.release()
                    except board.Conflict as exc:
                        raise board.Conflict('Codex 执行仍由外部宿主持有或原生进程仍存活，请到原工具停止后重试') from exc
                    except (OSError, ValueError) as exc:
                        raise board.Conflict('无法确认外部 Codex 执行已退出，请到原工具确认停止') from exc
            if external_claude:
                try:
                    agents = self._external_agents()
                except board.Conflict as exc:
                    raise board.Conflict('无法确认外部 Claude 执行已退出，请到原工具确认停止') from exc
                if any(agent['sessionId'] in external_claude for agent in agents):
                    raise board.Conflict('Claude 会话仍在外部运行，Crew 没有持有该进程，请到原工具停止后重试')
            for key in keys[1:]:
                if goals.get_execution(conn, key)['status'] not in goals.TERMINAL:
                    goals.set_execution_status(conn, key, 'paused')
            for goal_id in paused:
                self.goal_controller.sync(conn, goal_id)
            self.journal(session_id).emit('manual_stop', stopped_by=actor, text='已停止此人员的所有托管执行；排队消息不能自动恢复。')
            board.record_event(conn, 'session:stopped', target['project'], session_id, {'actor': actor, 'executions': keys[1:]})
            return {'ok': True, 'session_id': session_id, 'stopped': True, 'running': False,
                    'execution_ids': keys[1:]}

    def detach(self, session_id):
        """Release an idle owned session by letting its own process finish."""
        with self.lock:
            if not self.is_owned(session_id):
                raise board.BoardError("网页服务没有管理这个会话进程")
            if self.busy(session_id):
                raise board.Conflict("会话正在回复，请等这一轮结束")
            current = self.processes[session_id]
            if getattr(current, "runtime", "claude") == "codex":
                current.close()
                if not current.reader_done.wait(10):
                    raise board.BoardError("Codex 进程输出尚未排空")
                return
            with current.input_lock:
                if current.process.poll() is None:
                    current.stop_reason = 'detach'
                try:
                    current.process.stdin.close()
                except OSError:
                    current.stop_reason = None
                    raise
            try:
                current.process.wait(timeout=10)
            except subprocess.TimeoutExpired as exc:
                raise board.BoardError("会话关闭输入后仍未退出，请稍后查看进程状态") from exc
            if not current.reader_done.wait(10):
                raise board.BoardError("会话进程已退出，但输出尚未读完")

    def close(self):
        # Each native owns its own archive/transport deadlines. Waiting for one
        # under the router lock blocks callbacks and makes those waits additive.
        # Serialize repeated shutdown requests, but close independent carriers
        # outside that lock and retain every existing native wait/reader drain.
        with self._close_lock:
            with self.lock:
                self.closed = True
                closing_processes = list(self.processes.items())
                for journal in self.journals.values():
                    with journal.condition:
                        journal.condition.notify_all()

            def close_current(key, current):
                if current.process.poll() is None:
                    is_codex = getattr(current, "runtime", "claude") == "codex"
                    with current.input_lock:
                        runtime_recovery.record_service_close(current, key)
                        current.stop_reason = current.stop_reason or 'service_close'
                        if not is_codex:
                            try:
                                current.process.stdin.close()
                            except OSError:
                                if current.stop_reason == 'service_close':
                                    current.stop_reason = None
                                raise
                    if is_codex:
                        current.close()
                current.reader.join(timeout=1)

            errors = []
            if closing_processes:
                with ThreadPoolExecutor(max_workers=min(8, len(closing_processes)), thread_name_prefix='crew-native-close') as pool:
                    pending = [(current, pool.submit(close_current, key, current)) for key, current in closing_processes]
                    for current, result in pending:
                        try:
                            result.result()
                        except Exception as exc:
                            current.journal.emit('error', text='关闭原生执行失败：' + type(exc).__name__ + ': ' + str(exc))
                            errors.append(exc)
            if errors:
                raise errors[0]
