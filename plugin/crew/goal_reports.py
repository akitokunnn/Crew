"""Publish a task-local milestone without implying final completion."""
import hashlib

from . import board, goals


def ensure(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS goal_report_publications ('
                 'request_id TEXT PRIMARY KEY,goal_id TEXT NOT NULL REFERENCES goals(id),'
                 'report_id TEXT NOT NULL UNIQUE REFERENCES goal_reports(id),fingerprint TEXT NOT NULL,created_at TEXT NOT NULL)')


@goals.atomic
def publish(conn, goal_id, data, *, request_id, actor=board.HUMAN, execution_id=None,
            source=None, expected_version=None, now=None):
    goal = goals._row(conn, goal_id)
    goals._actor(conn, goal, actor)
    goals._text(request_id, '报告操作标识', limit=300)
    if not isinstance(data, dict):
        raise board.BoardError('阶段报告必须为结构化 report 对象')
    if source is not None and not isinstance(source, dict):
        raise board.BoardError('报告来源 source 必须是对象')
    if data.get('outcome', 'progress') != 'progress':
        raise board.BoardError('阶段报告 outcome 必须为 progress；最终报告请用 finish')
    if execution_id is not None:
        execution = goals.get_execution(conn, execution_id)
        if execution['session_id'] != actor or execution['goal_id'] != goal_id:
            raise board.BoardError('报告执行身份与任务不一致')
    fingerprint = hashlib.sha256(goals._json({'goal_id': goal_id, 'actor': actor,
        'execution_id': execution_id, 'report': data, 'source': source,
        'expected_version': expected_version}).encode('utf-8')).hexdigest()
    ensure(conn)
    previous = conn.execute('SELECT * FROM goal_report_publications WHERE request_id=?', (request_id,)).fetchone()
    if previous is not None:
        if previous['fingerprint'] != fingerprint:
            raise board.Conflict('这个报告操作标识已用于不同内容')
        return {'ok': True, 'report_id': previous['report_id'], 'report': goals.get_report(conn, previous['report_id']),
                'duplicate': True, 'pending_finish': False}
    if goal['status'] in goals.TERMINAL or goal['status'] == 'awaiting_acceptance':
        raise board.Conflict('本轮已经交付或结束；请先按反馈流程重新开启任务')
    finish = goals.get_finish(conn, goal_id)
    if finish and finish['status'] == 'pending':
        raise board.Conflict('最终报告正在收尾，不能再用阶段报告替换本轮交付')
    if execution_id is not None:
        gate = goals.dispatch_check(conn, goal_id, execution_id=execution_id)
        if not gate['allowed']:
            raise board.Conflict(gate['reason'])
    current = conn.execute('SELECT COALESCE(MAX(version),0) FROM goal_reports WHERE goal_id=? AND round=?',
                            (goal_id, goal['current_round'])).fetchone()[0]
    if expected_version is not None and (type(expected_version) is not int or current != expected_version):
        raise board.Conflict('报告已有新版本，请刷新后再发布')
    report = goals.save_report(conn, goal_id, {**data, 'outcome': 'progress', 'publication': {
        'stage': 'progress', 'actor': actor, 'execution_id': execution_id,
        'source': source or {'type': 'human' if actor == board.HUMAN else 'agent', 'id': actor},
        'request_id': request_id}}, now=now)
    conn.execute('INSERT INTO goal_report_publications VALUES(?,?,?,?,?)',
                 (request_id, goal_id, report['id'], fingerprint, board._time(now)))
    goals._touch(conn, goal_id, now)
    board._event(conn, 'goal:report-published', goal['project'], actor,
                 {'goal_id': goal_id, 'report_id': report['id'], 'round': goal['current_round'],
                  'requirement_version': goal['requirement_version'], 'stage': 'progress'}, now)
    return {'ok': True, 'report_id': report['id'], 'report': report, 'duplicate': False, 'pending_finish': False}
