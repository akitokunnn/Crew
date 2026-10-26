"""Deliver human answers only to the goal execution that asked for them."""
from . import board, codex_client, goals


@goals.atomic
def bind(conn, kind, item_id, execution_id):
    if kind not in ('decision', 'todo'):
        raise board.BoardError('未知目标事项类型')
    execution = goals.get_execution(conn, execution_id)
    table = 'decisions' if kind == 'decision' else 'todos'
    item = conn.execute('SELECT session_id FROM ' + table + ' WHERE id=?', (item_id,)).fetchone()
    if item is None or item['session_id'] != execution['session_id']:
        raise board.BoardError('事项与目标执行提出者不匹配')
    gate = goals.dispatch_check(conn, execution['goal_id'], execution_id=execution_id)
    if not gate['allowed']:
        raise board.Conflict(gate['reason'])
    conn.execute('INSERT INTO goal_links(kind,item_id,goal_id,round,requirement_version,execution_id) VALUES(?,?,?,?,?,?)',
                 (kind, item_id, execution['goal_id'], execution['round'], execution['requirement_version'], execution_id))


def get(conn, kind, item_id):
    row = conn.execute('SELECT * FROM goal_links WHERE kind=? AND item_id=?', (kind, item_id)).fetchone()
    return dict(row) if row else None


def _notice(controller, execution_id, identity, text):
    journal = controller.manager.journal(execution_id)
    # A pending native receipt may stay unknown across many service ticks.
    if any(event.get('human_answer_id') == identity and event.get('text') == text for event in journal.events):
        return
    journal.emit('error',human_answer_id=identity,text=text)


def _receipt(controller, conn, link, identity):
    receipts = []
    for row in conn.execute('SELECT id FROM goal_executions WHERE goal_id=? AND session_id=? ORDER BY round DESC,generation DESC',
                            (link['goal_id'],goals.get_execution(conn,link['execution_id'])['session_id'])):
        prior = controller.manager.journal(row['id']).inputs.get(identity)
        if prior is not None:
            receipts.append(prior)
    accepted = next((item for item in receipts if item['status'] == 'accepted'),None)
    return accepted or next((item for item in receipts if item['status'] in ('unknown','submitted')),None)


@goals.atomic
def _mark_delivered(conn, link, version):
    conn.execute('UPDATE goal_links SET delivered_version=? WHERE kind=? AND item_id=?',(version,link['kind'],link['item_id']))
    if link['kind'] == 'decision':
        conn.execute('UPDATE decisions SET delivered_at=? WHERE id=?',(board._time(None),link['item_id']))


def deliver(controller, conn):
    for row in conn.execute('SELECT * FROM goal_links'):
        link = dict(row)
        table = 'decisions' if link['kind'] == 'decision' else 'todos'
        item = conn.execute('SELECT * FROM ' + table + ' WHERE id=?', (link['item_id'],)).fetchone()
        if item is None:
            continue
        version = 1 if link['kind'] == 'decision' else item['version']
        if version <= link['delivered_version'] or link['kind'] == 'decision' and item['status'] != 'answered':
            continue
        if link['kind'] == 'todo' and item['status'] == 'open' and version == 1:
            continue
        if link['kind'] == 'todo' and item['status'] == 'withdrawn' and item['resolved_by'] == item['session_id']:
            # An AI withdrawal is its own decision, never a new human answer.
            _mark_delivered(conn, link, version)
            continue
        goal = goals.get(conn, link['goal_id'])
        if goal['current_round'] != link['round'] or goal['requirement_version'] != link['requirement_version'] or goal['status'] in goals.TERMINAL:
            with goals.transaction(conn):
                conn.execute('UPDATE goal_links SET delivered_version=? WHERE kind=? AND item_id=?',
                             (version, link['kind'], link['item_id']))
                board._event(conn,'goal:human-answer-archived',goal['project'],board.HUMAN,
                             {'kind':link['kind'],'id':link['item_id'],'version':version,'goal_id':goal['id'],
                              'reason':'原轮次/需求版本已经失效或目标已经结束'},None)
            continue
        if goal['status'] in ('paused', 'pausing') or goal['manual_pause']:
            continue
        original = goals.get_execution(conn, link['execution_id'])
        identity = 'human-answer:%s:%s:%s' % (link['kind'], item['id'], version)
        prior = _receipt(controller,conn,link,identity)
        if prior and prior['status'] == 'accepted':
            _mark_delivered(conn,link,version)
            continue
        try:
            execution = controller.current(conn,goal['id'],original['session_id']) or original
        except board.BoardError as error:
            _notice(controller,original['id'],identity,'人工答复无法投递：'+type(error).__name__+': '+str(error)[:500])
            continue
        if controller.manager.busy(execution['id']):
            continue
        if link['kind'] == 'decision':
            text = '用户答复拍板 #%s：%s\n答复：%s' % (item['id'], item['question'], item['answer'])
        else:
            text = '人工待办 #%s「%s」已更新：%s\n备注：%s' % (item['id'], item['title'], item['status'], item['note'] or '无')
        if prior and prior['status'] in ('unknown','submitted'):
            # Keep ambiguous native delivery visible, never replay a human answer blindly.
            detail = (prior.get('error') or 'NativeReceiptUnknown')+': '+(prior.get('detail') or '原生接收未确认，请先核对会话')
            _notice(controller,execution['id'],identity,'人工答复接收状态未确认，已保留，等待核对。'+detail[:500])
            continue
        source = 'crew' if link['kind'] == 'todo' and item['resolved_by'] != board.HUMAN else 'human'
        if goal['status'] == 'waiting_user':
            goals.resume(conn, goal['id'], actor=item['resolved_by'] if source == 'crew' else board.HUMAN)
        try:
            result = controller.send(goal['id'], execution['session_id'], text, identity=identity, source=source)
            if result.get('accepted') is not True:
                raise board.Conflict('NativeReceiptUnknown: 原生接收未确认，请先核对会话')
        except (board.BoardError,codex_client.CodexError,OSError) as error:
            _notice(controller,execution['id'],identity,'人工答复投递失败：'+type(error).__name__+': '+str(error)[:500])
            continue
        _mark_delivered(conn,link,version)
