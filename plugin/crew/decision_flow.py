"""Human-requested decision disclosure. Reading state never queues model work."""
from . import board, decision_briefs, goals, trust_mode

_READ = object()


def ensure(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS decision_presentations ("
                 "decision_id INTEGER PRIMARY KEY REFERENCES decisions(id),"
                 "stage TEXT NOT NULL DEFAULT 'notice' CHECK(stage IN ('notice','discussion','ready')),"
                 "discussed_at TEXT,ready_at TEXT,notice_id INTEGER REFERENCES leader_notices(id),updated_at TEXT NOT NULL)")


def _exists(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='decision_presentations'").fetchone() is not None


def _decision(conn, decision_id, project=None, *, actor=_READ):
    if actor is not _READ and actor != board.HUMAN:
        raise board.BoardError('拍板的讨论和展示阶段只能由用户明确请求')
    value = decision_briefs._decision(conn, decision_id)
    if project is not None and value['project'] != project:
        raise board.BoardError('拍板不属于当前工作区')
    return value


def _owner(conn, decision):
    leader_id = trust_mode.decision_leader(conn, decision['id'])
    if leader_id is None:
        raise board.Conflict('这项拍板没有可用的所属 leader，请先整理会话归属')
    leader = board.session(conn, leader_id)
    if (leader['project'] != decision['project'] or leader['role'] != 'leader'
            or leader['status'] == 'closed' or board.is_archived(conn, leader_id)):
        raise board.Conflict('这项拍板的原负责 leader 已结束，请先确认后续负责人')
    return leader_id


def get(conn, decision_id, project=None):
    decision = _decision(conn, decision_id, project)
    row = conn.execute('SELECT * FROM decision_presentations WHERE decision_id=?', (decision_id,)).fetchone() if _exists(conn) else None
    return {'id':decision_id,'presentation_stage':row['stage'] if row else 'notice',
            'discussed_at':row['discussed_at'] if row else None,'ready_at':row['ready_at'] if row else None,
            'notice_id':row['notice_id'] if row else None,'leader_session_id':trust_mode.decision_leader(conn,decision['id'])}


def enrich(conn, decisions):
    one = isinstance(decisions, dict)
    values = [decisions] if one else decisions
    result = [{**item, **get(conn,item['id'])} for item in values]
    return result[0] if one else result


@goals.atomic
def discuss(conn, decision_id, project=None, *, actor=board.HUMAN, now=None):
    decision = _decision(conn,decision_id,project,actor=actor)
    if decision['status'] != 'open':
        raise board.Conflict('这项拍板已经结束，无需再次请求讨论')
    leader_id = _owner(conn,decision)
    ensure(conn)
    before = get(conn,decision_id)
    if before['presentation_stage'] != 'notice':
        return before
    text = ('用户点击了“听它说明”，明确要求讨论这项拍板（内部 ID：%s）。\n'
            '原问题：%s\n先在本人对话里，用简明语言解释背景、为什么需要用户，以及主要取舍，回应用户的问题。'
            '此时不要直接堆出完整拍板表或另发重复题；聊清楚后由用户点击“给我选项”。'
            '原 ID 不变，若结构化 brief 不完整请在讨论后原位补齐，不替用户作选择。' % (decision_id,decision['question']))
    stamp = board._time(now)
    notice = board._queue_leader_notice(conn,leader_id,text,'web',now)
    conn.execute("INSERT INTO decision_presentations(decision_id,stage,discussed_at,notice_id,updated_at) VALUES(?,'discussion',?,?,?) "
                 "ON CONFLICT(decision_id) DO UPDATE SET stage='discussion',discussed_at=excluded.discussed_at,notice_id=excluded.notice_id,updated_at=excluded.updated_at",
                 (decision_id,stamp,notice,stamp))
    board._event(conn,'decision:discussion',decision['project'],board.HUMAN,{'id':decision_id,'notice_id':notice,'leader_session_id':leader_id},now)
    return get(conn,decision_id)


@goals.atomic
def ready(conn, decision_id, project=None, *, actor=board.HUMAN, now=None):
    decision = _decision(conn,decision_id,project,actor=actor)
    if decision['status'] != 'open':
        raise board.Conflict('这项拍板已经结束，不能重新展开选项')
    _owner(conn,decision)
    ensure(conn)
    before = get(conn,decision_id)
    if before['presentation_stage'] == 'notice' or before['discussed_at'] is None:
        raise board.Conflict('请先听 leader 说明并讨论，再请求完整选项')
    brief = decision_briefs.get(conn,decision_id)
    if brief is None or not decision['recommendation']:
        raise board.Conflict('说明还不完整，请 leader 补齐推荐、理由和每项后果后再给选项')
    try:
        decision_briefs.validate(brief,options=decision['options'],recommendation=decision['recommendation'])
    except board.BoardError as error:
        raise board.Conflict('拍板说明还不完整：'+str(error)) from error
    if before['presentation_stage'] == 'ready':
        return before
    stamp = board._time(now)
    conn.execute("UPDATE decision_presentations SET stage='ready',ready_at=?,updated_at=? WHERE decision_id=?",(stamp,stamp,decision_id))
    board._event(conn,'decision:ready',decision['project'],board.HUMAN,{'id':decision_id},now)
    return get(conn,decision_id)
