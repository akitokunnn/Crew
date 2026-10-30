"""Exclusive, explicit handoff of a saved Codex conversation to the desktop.

The controlled bridge confirms its own readiness and release. Only this state
machine authorizes Crew to resume writing; an OS deep link is not confirmation.
"""
from contextlib import closing
from pathlib import Path
from urllib.parse import quote
import threading
import uuid

from . import board, codex_client, codex_desktop, goals, native_home, runtimes, store


# Production backend initialization is already serialized by HomeServiceLock.
# This registry additionally distinguishes a live transition from an unfinished
# one when another Manager is constructed inside this same process.
_TRANSITION_LOCK = threading.RLock()
_ACTIVE_TOKENS = set()


def recover_interrupted(home):
    """Expose explicit return after a previous host stopped mid-transition.

    Preserve the native binding and external gate. This does not inspect/control
    the desktop, launch a runtime, or claim which native process is still alive.
    """
    if not store.db_path(home).is_file():
        return []
    recovered = []
    with _TRANSITION_LOCK, closing(store.connect(home)) as conn, board.transaction(conn):
        rows = conn.execute("SELECT scope_id,state,token,last_error FROM native_session_controls "
                            "WHERE state IN ('releasing','returning')").fetchall()
        for row in rows:
            if row['token'] in _ACTIVE_TOKENS:
                continue
            reason = '上次原生交接在%s阶段中断；仍保留外部控制，请明确点击“回到 Crew”重新核对。' % row['state']
            if row['last_error']:
                reason += ' 原因：' + row['last_error']
            conn.execute("UPDATE native_session_controls SET state='external',token=NULL,last_error=?,updated_at=? "
                         'WHERE scope_id=? AND state=? AND token IS ?',
                         (reason, store.now_iso(), row['scope_id'], row['state'], row['token']))
            recovered.append(row['scope_id'])
    return recovered


def scope(session_id, execution_id=None):
    return execution_id or session_id


def _binding(conn, session_id, execution_id=None):
    board.session(conn, session_id)
    if execution_id is None:
        return runtimes.get(conn, session_id)
    execution = goals.get_execution(conn, execution_id)
    if execution['session_id'] != session_id:
        raise board.BoardError('原生执行不属于此会话')
    binding = next((item for item in execution['bindings'] if item['runtime'] == execution['runtime']), None)
    return {'runtime': execution['runtime'], 'native_id': binding['native_id'] if binding else None}


def describe(conn, session_id, execution_id=None):
    binding = _binding(conn, session_id, execution_id)
    row = conn.execute('SELECT * FROM native_session_controls WHERE scope_id=?',
                       (scope(session_id, execution_id),)).fetchone()
    value = dict(row) if row is not None else {
        'scope_id': scope(session_id, execution_id), 'session_id': session_id, 'execution_id': execution_id,
        'native_id': binding['native_id'], 'native_home': str(native_home.codex_home()),
        'state': 'crew', 'last_error': None}
    value.pop('token', None)
    value['runtime'] = binding['runtime']
    value['url'] = ('codex://threads/' + quote(value['native_id'], safe='')) if value['native_id'] else None
    value['external'] = value['state'] != 'crew'
    return value


def assert_control(conn, session_id, execution_id=None, token=None):
    row = conn.execute('SELECT state,token FROM native_session_controls WHERE scope_id=?',
                       (scope(session_id, execution_id),)).fetchone()
    if row is not None and row['state'] != 'crew':
        if token and row['state'] in ('releasing', 'returning') and token == row['token']:
            return
        raise board.Conflict('此对话已交给 Codex 桌面，请先点击“回到 Crew”；排队任务会继续保留')


def for_scope(conn, scope_id):
    execution = conn.execute('SELECT session_id FROM goal_executions WHERE id=?', (scope_id,)).fetchone()
    if execution is not None:
        return describe(conn, execution['session_id'], scope_id)
    if conn.execute('SELECT 1 FROM sessions WHERE session_id=?', (scope_id,)).fetchone():
        return describe(conn, scope_id)
    return None


def blocked(conn, session_id, execution_id=None):
    row = conn.execute('SELECT state FROM native_session_controls WHERE scope_id=?',
                       (scope(session_id, execution_id),)).fetchone()
    return row is not None and row['state'] != 'crew'


def home_for(conn, session_id, execution_id=None):
    row = conn.execute('SELECT native_home FROM native_session_controls WHERE scope_id=?',
                       (scope(session_id, execution_id),)).fetchone()
    return Path(row['native_home']) if row is not None else native_home.codex_home()


def remember(conn, session_id, native_id, home, execution_id=None):
    key = scope(session_id, execution_id)
    row = conn.execute('SELECT native_id,native_home FROM native_session_controls WHERE scope_id=?', (key,)).fetchone()
    if row is not None and (row['native_id'] != native_id or Path(row['native_home']) != Path(home)):
        raise board.Conflict('Codex 原生身份或 HOME 与已保存的绑定不一致，未覆盖原绑定')
    conn.execute('INSERT INTO native_session_controls(scope_id,session_id,execution_id,native_id,native_home,state,updated_at) '
                 "VALUES(?,?,?,?,?,'crew',?) ON CONFLICT(scope_id) DO NOTHING",
                 (key, session_id, execution_id, native_id, str(home), store.now_iso()))


def _begin(conn, session_id, execution_id, state, *, reopen=False):
    with _TRANSITION_LOCK:
        return _begin_locked(conn, session_id, execution_id, state, reopen=reopen)


def _begin_locked(conn, session_id, execution_id, state, *, reopen=False):
    value = describe(conn, session_id, execution_id)
    previous = conn.execute('SELECT token FROM native_session_controls WHERE scope_id=?', (value['scope_id'],)).fetchone()
    if previous is not None and previous['token'] in _ACTIVE_TOKENS:
        raise board.Conflict('原生交接正在进行，请等待当前操作结束')
    binding = _binding(conn, session_id, execution_id)
    if binding['runtime'] != 'codex' or not binding['native_id']:
        raise board.BoardError('只有已保存原生 ID 的 Codex 对话才能在桌面接续')
    if value['native_id'] != binding['native_id']:
        raise board.Conflict('Codex 原生绑定已变化，未打开另一个会话')
    remember(conn, session_id, binding['native_id'], value['native_home'], execution_id)
    if state == 'releasing' and value['state'] != 'crew' and not (reopen and value['state'] == 'external'):
        raise board.Conflict('此对话已交给 Codex 桌面或正在切换')
    if state == 'returning' and value['state'] == 'crew':
        raise board.Conflict('此对话已经由 Crew 控制')
    token = uuid.uuid4().hex
    changed = conn.execute('UPDATE native_session_controls SET state=?,token=?,last_error=NULL,updated_at=? '
                           'WHERE scope_id=? AND state=?',
                           (state, token, store.now_iso(), value['scope_id'], value['state'])).rowcount
    if changed != 1:
        raise board.Conflict('原生控制状态已变化，请刷新后重试')
    _ACTIVE_TOKENS.add(token)
    return token


def _end_transition(token):
    with _TRANSITION_LOCK:
        _ACTIVE_TOKENS.discard(token)


def _finish(conn, session_id, execution_id, token, state, error=None):
    changed = conn.execute('UPDATE native_session_controls SET state=?,token=NULL,last_error=?,updated_at=? '
                           'WHERE scope_id=? AND token=?',
                           (state, str(error) if error is not None else None, store.now_iso(),
                            scope(session_id, execution_id), token)).rowcount
    if changed != 1:
        raise board.Conflict('原生控制状态已变化，未覆盖新的控制者')
    return describe(conn, session_id, execution_id)


def _target(manager, conn, session_id, execution_id):
    person = board.session(conn, session_id)
    if execution_id:
        execution = goals.get_execution(conn, execution_id)
        if execution['session_id'] != session_id:
            raise board.BoardError('原生执行不属于此会话')
        person = manager.goal_controller.target(conn, execution)
    spec, _ = manager._resume_spec(conn, person)
    return person, spec


def release(manager, session_id, execution_id=None):
    """Release idle ownership to the controlled desktop; never starts a turn."""
    key = scope(session_id, execution_id)
    with manager.lock:
        if manager.busy(key) or manager.maintenance_held(session_id):
            raise board.Conflict('会话正在执行，请等这一轮结束后再到 Codex 桌面继续')
        with closing(store.connect(manager.home)) as conn:
            value = describe(conn, session_id, execution_id)
            role = board.session(conn, session_id)['role']
    # Preflight before changing native instructions/processes. Desktop discovery
    # and readiness can take seconds; they must not block other conversations.
    desktop = codex_desktop.prepare(manager.home, {**value, 'role': role})
    with manager.lock:
        if manager.busy(key) or manager.maintenance_held(session_id):
            raise board.Conflict('会话正在执行，请等这一轮结束后再到 Codex 桌面继续')
        with closing(store.connect(manager.home)) as conn:
            latest = describe(conn, session_id, execution_id)
            if (any(latest[field] != value[field] for field in ('native_id', 'native_home', 'state'))
                    or board.session(conn, session_id)['role'] != role):
                raise board.Conflict('原生会话或控制状态在桌面预检期间变化，请刷新后重试')
        duplicate = latest['state'] == 'external'
        if duplicate:
            previous = manager.processes.get(key)
            if previous is not None and not previous.reader_done.is_set():
                raise board.Conflict('原生进程输出尚未排空，尚不能在桌面继续')
        with closing(store.connect(manager.home)) as conn:
            token = _begin(conn, session_id, execution_id, 'releasing', reopen=duplicate)
    current = None
    released = duplicate
    try:
        if not duplicate:
            with manager.lock:
                current = manager.processes.get(key) if manager.running(key) else None
                if current is None:
                    with closing(store.connect(manager.home)) as conn:
                        target, spec = _target(manager, conn, session_id, execution_id)
                    manager._launch_native(target, {**spec, '_native_control_token': token}, resume=True)
                    current = manager.processes[key]
                if getattr(current, 'runtime', None) != 'codex':
                    raise board.BoardError('当前受控进程不是 Codex')
                current.prepare_desktop_handoff()
                current.close(archive=False)
                released = True
                if not current.reader_done.wait(10):
                    raise board.BoardError('Codex 进程输出尚未排空，尚不能在桌面继续')
        desktop.open()
        with manager.lock, closing(store.connect(manager.home)) as conn:
            result = _finish(conn, session_id, execution_id, token, 'external')
        return {**result, 'ok': True, 'desktop_opened': True, **({'duplicate': True} if duplicate else {})}
    except Exception as exc:
        with manager.lock:
            if not released and current is not None and manager.running(key):
                try:
                    current.restore_crew_instructions()
                except Exception as restore_error:
                    current.close(archive=False)
                    released = True
                    exc = board.BoardError(str(exc) + '；恢复 Crew 指令失败：' + str(restore_error))
            with closing(store.connect(manager.home)) as conn:
                _finish(conn, session_id, execution_id, token, 'external' if released else 'crew', exc)
        raise
    finally:
        _end_transition(token)


def reclaim(manager, session_id, execution_id=None):
    """Explicitly return the same thread; failed sync keeps the external gate."""
    with manager.lock:
        key = scope(session_id, execution_id)
        with closing(store.connect(manager.home)) as conn:
            value = describe(conn, session_id, execution_id)
            if value['state'] == 'crew':
                return {**value, 'ok': True, 'duplicate': True}
            if manager.running(key):
                raise board.Conflict('Crew 原生进程尚未退出，不能重复接管')
            token = _begin(conn, session_id, execution_id, 'returning')
    try:
        # None denotes a legacy external desktop. Errors resolving a saved
        # controlled identity are intentionally not treated as legacy.
        desktop = codex_desktop.existing(manager.home, value)
        if desktop is not None:
            desktop.cede()
        with manager.lock:
            with closing(store.connect(manager.home)) as conn:
                assert_control(conn, session_id, execution_id, token)
                target, spec = _target(manager, conn, session_id, execution_id)
            manager._launch_native(target, {**spec, '_native_control_token': token, '_native_history_sync': True}, resume=True)
        if desktop is not None:
            desktop.reclaimed()
        with manager.lock, closing(store.connect(manager.home)) as conn:
            result = _finish(conn, session_id, execution_id, token, 'crew')
        return {**result, 'ok': True}
    except Exception as exc:
        failure = exc
        if (isinstance(exc, codex_client.RpcError) and exc.method == 'thread/resume'
                and 'already has an active writer' in str(exc)):
            failure = board.Conflict('Codex 原生客户端仍持有此对话的写入连接。请先结束那边的执行并关闭持有此对话的原生客户端，'
                '再点击“回到 Crew”。仅离开页面可能不会释放连接；原对话、历史和排队消息均已保留。原生原因：' + str(exc))
        elif isinstance(exc, codex_client.RpcError):
            failure = board.Conflict('无法接回 Codex 会话。原生原因：' + str(exc))
        with manager.lock:
            current = manager.processes.get(key)
            if current is not None and manager.running(key):
                current.close(archive=False)
                current.reader_done.wait(10)
            with closing(store.connect(manager.home)) as conn:
                _finish(conn, session_id, execution_id, token, 'external', failure)
        if failure is not exc:
            raise failure from exc
        raise
    finally:
        _end_transition(token)
