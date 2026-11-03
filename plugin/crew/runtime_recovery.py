"""Bounded continuation of interrupted owned carriers on the existing agents poll."""
import datetime as dt
import subprocess

from . import board, codex_client, goals, messaging, native_handoff, runtimes, store


BACKOFF_SECONDS = (5, 15, 45)
CONTINUATION = ('[Crew] 原生载体意外退出，本轮未完成。继续同一个原生会话，先核对已有历史、实际状态与执行证据，'
                '仅续接尚未完成的工作。已成功的操作不能重做，接收结果未知的操作先核实，不能推测失败后重发。'
                '若其实已经完成，就照常收尾，并保持原授权和任务范围。')


def _scope(current, key):
    return {'session_id': current.execution['session_id'] if current.execution else key,
            'execution_id': key if current.execution else None, 'runtime': current.runtime,
            'native_id': current.thread_id if current.runtime == 'codex' else getattr(current, 'native_id', key)}


def record_exit(current, key):
    """Persist the interrupted scope before the reader reports its output drained."""
    current.unexpected_exit = (current.journal.status == 'working' and current.stop_reason is None
                               and not current.interrupt_requested and current.maintenance is None)
    if current.unexpected_exit:
        current.journal.emit('runtime_recovery', status='interrupted', scope=_scope(current, key))


def record_interrupt(current, key):
    if not current.interrupt_requested:
        current.interrupt_requested = True
        current.journal.emit('runtime_recovery', status='cancelled', scope=_scope(current, key), reason='user_interrupt')


def _progress(journal):
    """Only completed work or new explicit user input resets consecutive failures."""
    attempts, uncertain = 0, False
    for event in reversed(journal.events):
        if event['type'] == 'turn_result' and event.get('success'):
            break
        if event['type'] == 'runtime_recovery' and event['status'] == 'attempting':
            attempts += 1
        if event['type'] == 'input_delivery':
            latest = journal.inputs.get(event['message_id'], event)
            uncertain |= latest['status'] in ('unknown', 'submitted')
        if event['type'] == 'message' and event.get('source') in ('human', 'qq', 'lark'):
            break
    return attempts, uncertain


class Controller:
    def __init__(self, manager):
        self.manager = manager
        self.pending = {}
        self.restored = False
        self.startup_exclusions = set()

    def _finish(self, key, status, text):
        journal = self.manager.journal(key)
        pending = self.pending.pop(key)
        journal.emit('runtime_recovery', status=status, scope=pending['scope'], message_id=pending['identity'])
        journal.emit('error', text=text)

    def _target(self, conn, key, pending):
        scope, current = pending['scope'], pending['current']
        logical = scope['session_id']
        session = board.session(conn, logical)
        if (session['status'] == 'closed' or board.is_archived(conn, logical)
                or session['project'] not in {project['name'] for project in board.projects(conn)}
                or messaging.stopped(conn, logical) or current is not None and (
                    current.stop_reason is not None or current.interrupt_requested)):
            raise board.BoardError('会话已停止、归档、移除或工作区已停用')
        if native_handoff.blocked(conn, logical, scope['execution_id']):
            raise board.BoardError('会话已交给外部载体，保留外部控制权')
        if scope['execution_id']:
            execution = goals.get_execution(conn, key)
            gate = goals.dispatch_check(conn, execution['goal_id'], execution_id=key, allow_discussion=True)
            if not gate['allowed']:
                raise board.BoardError(gate['reason'])
            mapping = {'runtime': execution['runtime'], 'native_id': next(
                (binding['native_id'] for binding in execution['bindings'] if binding['runtime'] == execution['runtime']), None)}
            session = self.manager.goal_controller.target(conn, execution)
        else:
            mapping = runtimes.get(conn, logical)
        original = scope['native_id']
        if mapping['runtime'] != scope['runtime'] or not original or mapping['native_id'] != original:
            raise board.BoardError('原生会话绑定已更改或缺失，不能自动新建或切换身份')
        return session, original

    def _queue(self, key, event, current, now):
        journal = self.manager.journal(key)
        attempts, uncertain = _progress(journal)
        receipt = journal.inputs.get(event.get('message_id'))
        identity = (event.get('message_id') if not receipt or receipt['status'] != 'accepted' else None)
        due = event.get('retry_at')
        pending = {'current': current, 'scope': event['scope'], 'seq': len(journal.events),
                   'identity': identity or 'crash:' + key + ':' + str(len(journal.events)),
                   'due': store.parse_iso(due) if due else now + dt.timedelta(seconds=BACKOFF_SECONDS[min(attempts, 2)])}
        self.pending[key] = pending
        self.startup_exclusions.add(key)
        if self.manager.auto_candidates is not None:
            self.manager.auto_candidates.pop(key, None)
        if uncertain:
            self._finish(key, 'blocked', '自动续回已停止。上一条输入的原生接收结果未知，请核对会话后明确继续。')
        elif attempts >= len(BACKOFF_SECONDS):
            self._finish(key, 'exhausted', '载体连续失败，3 次自动续回额度已用完。请检查上方真实错误后手动继续。')
        else:
            journal.emit('runtime_recovery', status='queued', attempt=attempts + 1, scope=pending['scope'],
                         message_id=pending['identity'], retry_at=pending['due'].isoformat())
            journal.emit('error', text='原生载体意外退出，将在至少 %s 秒后尝试第 %s/3 次自动续回。' %
                         (BACKOFF_SECONDS[attempts], attempts + 1))

    def recover(self, conn, agents, now, runtime=None):
        """Caller holds Manager.lock and its background work admission."""
        now = store.parse_iso(now)
        if not self.restored:
            self.restored = True
            for path in sorted((self.manager.home / 'chat').glob('*.jsonl')):
                key = path.stem
                if key in self.manager.processes:
                    continue
                journal = self.manager.journal(key)
                event = next((event for event in reversed(journal.events) if event['type'] == 'runtime_recovery'), None)
                if event is None or 'scope' not in event:
                    continue
                if any(e['type'] == 'message' and e['id'] != event.get('message_id')
                       for e in journal.events[event['seq']:]):
                    continue
                self.startup_exclusions.add(key)
                if self.manager.auto_candidates is not None:
                    self.manager.auto_candidates.pop(key, None)
                if event['status'] not in ('interrupted', 'queued', 'attempting', 'retrying', 'resumed'):
                    continue
                if event['status'] == 'resumed' and journal.status != 'working':
                    continue
                self._queue(key, event, None, now)
        for key, current in list(self.manager.processes.items()):
            if (key in self.pending or not getattr(current, 'unexpected_exit', False) or current.process.poll() is None
                    or not current.reader_done.is_set()):
                continue
            current.unexpected_exit = False
            journal = self.manager.journal(key)
            event = next(event for event in reversed(journal.events)
                         if event['type'] == 'runtime_recovery' and event['status'] == 'interrupted')
            self._queue(key, event, current, now)

        live = {agent['sessionId'] for agent in agents}
        for key, pending in list(self.pending.items()):
            current = pending['current']
            scope = pending['scope']
            if runtime is not None and scope['runtime'] != runtime:
                continue
            if self.manager.processes.get(key) is not current:
                self.pending.pop(key)
                continue
            try:
                session, native_id = self._target(conn, key, pending)
            except board.BoardError as exc:
                self._finish(key, 'cancelled', '自动续回已取消（%s）。' % exc)
                continue
            if now < pending['due'] or scope['runtime'] == 'claude' and native_id in live:
                continue
            journal = self.manager.journal(key)
            if any(event['type'] == 'message' and event['id'] != pending['identity']
                   for event in journal.events[pending['seq']:]):
                self.pending.pop(key)
                continue
            attempts, uncertain = _progress(journal)
            if uncertain:
                self._finish(key, 'blocked', '自动续回已停止。原生接收结果未知，请核对会话后明确继续。')
                continue
            attempt = attempts + 1
            journal.emit('runtime_recovery', status='attempting', attempt=attempt, message_id=pending['identity'], scope=scope)
            try:
                # Keep the exact execution and native binding. Normal goal dispatch
                # may rotate context and create a generation, which is inappropriate here.
                result = self.manager.send(session, CONTINUATION, source='crew', identity=pending['identity'],
                                           resume_existing=True)
                if result.get('accepted') is not True:
                    raise board.Conflict('原生载体未确认接收续回输入')
            except (board.BoardError, codex_client.CodexError, OSError, subprocess.SubprocessError) as exc:
                detail = type(exc).__name__ + ': ' + str(exc)
                receipt = journal.inputs.get(pending['identity'])
                if receipt and receipt['status'] in ('unknown', 'submitted'):
                    self._finish(key, 'blocked', '续回输入的接收结果未知，自动重试已停止（%s）。' % detail)
                elif attempt >= len(BACKOFF_SECONDS):
                    self._finish(key, 'exhausted', '3 次自动续回均失败（%s），请检查错误后手动继续。' % detail)
                else:
                    pending['current'] = self.manager.processes.get(key)
                    pending['due'] = now + dt.timedelta(seconds=BACKOFF_SECONDS[attempt])
                    journal.emit('runtime_recovery', status='retrying', attempt=attempt, scope=scope,
                                 message_id=pending['identity'], retry_at=pending['due'].isoformat())
                    journal.emit('error', text='第 %s/3 次自动续回失败（%s），至少 %s 秒后重试。' %
                                 (attempt, detail, BACKOFF_SECONDS[attempt]))
                continue
            journal.emit('runtime_recovery', status='resumed', attempt=attempt, message_id=pending['identity'], scope=scope)
            self.pending.pop(key)
