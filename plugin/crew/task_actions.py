"""User task controls and durable native-operation receipts, shared by callers."""
from . import board, goals, tasks


def effect(conn, action, data, operation, *, validate=None):
    with goals.transaction(conn):
        prior = tasks.previous_request(conn, action, data)
        if prior is not None:
            return prior
        if validate is not None:
            validate()
        at = board._time(None)
        conn.execute('INSERT INTO task_requests VALUES(?,?,?,?,?,?,?,?)',
                     (data['request_id'], action, tasks.request_digest(action, data), 'submitted', None, None, at, at))
    try:
        result = operation()
    except Exception as exc:
        # A native effect can precede a transport error. Persist uncertainty and
        # preserve the exact cause; never relaunch with the same request ID.
        conn.execute("UPDATE task_requests SET status='unknown',error=?,updated_at=? WHERE request_id=?",
                     (type(exc).__name__ + ': ' + str(exc), board._time(None), data['request_id']))
        if data.get('task_id'):
            conn.execute('UPDATE goals SET reason=? WHERE id=?',
                ('请求尚未确认完成：' + type(exc).__name__ + ': ' + str(exc), data['task_id']))
        raise
    conn.execute("UPDATE task_requests SET status='completed',response_json=?,updated_at=? WHERE request_id=?",
                 (goals._json(result), board._time(None), data['request_id']))
    return result


def control(conn, action, data, *, pause, send, schedule, stop_discussions, now=None):
    identity = data.get('task_id')
    current = tasks.get(conn, identity)

    def validate():
        tasks._expected(conn, identity, data.get('version'), active=action != 'restore')
        if action == 'start' and current['authorized'] and current['execution_status'] != 'queued':
            raise board.Conflict('任务已经开始；停止的任务请明确恢复，已交付任务请在原对话补充要求')
        if action == 'resume' and current['execution_status'] not in ('paused', 'waiting_user', 'failed'):
            raise board.Conflict('只有已停止、等待用户或故障受阻的任务可以恢复')

    def apply():
        if action == 'stop':
            discussion_stop = stop_discussions(conn, identity)
            followup_only = goals.task_profile(conn, identity) and current['execution_status'] == 'awaiting_acceptance' and discussion_stop['matched']
            if current['execution_status'] not in goals.TERMINAL and not followup_only:
                pause(identity)
            return {'task': tasks.get(conn, identity), 'discussion_stop': discussion_stop}
        elif action in ('archive', 'trash'):
            tasks.request_lifecycle(conn, identity, 'archived' if action == 'archive' else 'trashed', defer=True, now=now)
            discussion_stop = stop_discussions(conn, identity)
            followup_only = goals.task_profile(conn, identity) and current['execution_status'] == 'awaiting_acceptance' and discussion_stop['matched']
            if current['execution_status'] not in goals.TERMINAL and current['execution_status'] != 'paused' and not followup_only:
                pause(identity)
            tasks.finalize_lifecycle_intents(conn, identity, now=now)
            return {'task': tasks.get(conn, identity), 'discussion_stop': discussion_stop}
        elif action == 'restore':
            return {'task': tasks.set_lifecycle(conn, identity, data.get('version'), 'active', now=now)}
        elif action in ('start', 'resume'):
            if action == 'resume':
                review = goals.resume_review(conn, identity, now=now)
                if review is not None:
                    return {'task': tasks.get(conn, identity), 'receipt': None, 'resumed': 'acceptance'}
            if not current['authorized']:
                tasks.authorize(conn, identity, data.get('version'), now=now)
            goal = goals.get(conn, identity)
            if goal['status'] in ('paused', 'waiting_user'):
                goals.resume(conn, identity, now=now)
            text = current['request'] if action == 'start' else '用户明确恢复任务。先核对已有成果与外部副作用，只推进尚未完成的授权工作。'
            receipt = send(identity, current['leader_session_id'], text,
                           identity='task-request:' + data['request_id'], source='human', intent='work')
            return {'task': tasks.get(conn, identity), 'receipt': receipt}
        elif action == 'schedule':
            receipt = schedule(identity, data.get('session_id') or current['leader_session_id'],
                data.get('text'), identity=data['request_id'], evidence=data.get('evidence'))
            return {'task': tasks.get(conn, identity), 'receipt': receipt}
        else:
            raise board.BoardError('未知任务控制操作')
        return {'task': tasks.get(conn, identity)}

    return effect(conn, 'task:' + action, data, apply, validate=validate)
