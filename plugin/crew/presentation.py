"""Workspace views composed from recorded facts, without changing the board."""
import json

from . import board


def attention(conn, sessions, decisions, tasks, journals):
    dismissed = {r[0] for r in conn.execute("SELECT item_id FROM attention_resolutions")}
    visible = {s['session_id']: s for s in sessions if s['status'] != 'closed'}
    items, asking = [], set()
    for d in decisions:
        if d['session_id'] not in visible:
            continue
        if d['status'] == 'open' or (d['status'] == 'answered' and not d['delivered_at']):
            asking.add(d['session_id'])
        if d['status'] != 'open':
            continue
        items.append(dict(id='decision:' + str(d['id']), kind='decision', priority=0 if d['blocking'] else 3,
                          session_id=d['session_id'], decision_id=d['id'], title=d['question'],
                          detail=d['recommendation'] or '', created_at=d['created_at']))
    for sid, s in visible.items():
        if s['status'] == 'waiting' and sid not in asking:
            items.append(dict(id='waiting:' + sid + ':' + str(s['last_event_at']), kind='waiting', priority=1,
                              session_id=sid, title=s['crew_name'] or s.get('title') or '会话等你回复',
                              detail=s['status_note'] or '请打开会话查看需要你处理的事', created_at=s['last_event_at']))
        events = journals.get(sid, [])
        last_message = max((e['seq'] for e in events if e['type'] == 'message'), default=0)
        error = next((e for e in reversed(events) if e['type'] == 'error' and e['seq'] > last_message), None)
        if error:
            items.append(dict(id='error:' + sid + ':' + str(error['seq']), kind='session_error', priority=1,
                              session_id=sid, title=(s['crew_name'] or '会话') + ' 出错了',
                              detail=error['text'], created_at=s['last_event_at']))
    for task in tasks:
        if task['status'] == 'failed':
            task_id = task['run_id'] or task['rollout_id']
            items.append(dict(id='codex:' + task_id, kind='codex_failed', priority=2,
                              session_id=task['session_id'], task_id=task_id, title=task['task_name'] or 'Codex 任务失败',
                              detail='退出码：' + str(task['exit_code']), created_at=task['ended_at'] or task['started_at']))
    return sorted((item for item in items if item['id'] not in dismissed), key=lambda x: (x['priority'], x['created_at'] or '', x['id']))


def timeline(conn, session_id, tasks):
    s = board.session(conn, session_id)
    items = []
    def add(identity, kind, at, title, body='', source='Crew', **extra):
        items.append(dict(id=identity, type=kind, at=at, title=title, body=body, source=source, **extra))
    for row in conn.execute('SELECT * FROM reports WHERE session_id=?', (session_id,)):
        add('report:' + str(row['id']), 'report', row['created_at'], '进展报告', row['body'])
    for row in conn.execute("SELECT h.* FROM handoffs h WHERE project=? AND (from_session=? OR EXISTS "
                            "(SELECT 1 FROM handoff_deliveries d WHERE d.handoff_id=h.id AND d.session_id=?) OR "
                            "(NOT EXISTS(SELECT 1 FROM handoff_deliveries d WHERE d.handoff_id=h.id) AND "
                            "(to_target='*' OR to_target=? OR to_target=?)))",
                            (s['project'], session_id, session_id, s['crew_name'], s['group_name'])):
        add('handoff:' + str(row['id']), 'handoff', row['created_at'],
            '发出的交接' if row['from_session'] == session_id else '收到的交接', row['body'])
    for row in conn.execute('SELECT * FROM leases WHERE holder_session=?', (session_id,)):
        add('lease:' + str(row['id']), 'lease', row['taken_at'], '借用 ' + row['resource'], row['purpose'], status='已归还' if row['released_at'] else '借用中')
        if row['released_at']:
            add('lease-return:' + str(row['id']), 'lease', row['released_at'], '归还 ' + row['resource'], row['release_note'] or '')
    for row in conn.execute('SELECT * FROM decisions WHERE session_id=?', (session_id,)):
        add('decision:' + str(row['id']), 'decision', row['created_at'], '提请拍板', row['question'], status=row['status'])
        if row['answered_at']:
            add('answer:' + str(row['id']), 'decision', row['answered_at'], '你的答复', row['answer'],
                source='飞书' if row['answered_by'] == 'feishu' else 'Crew', status='已送达' if row['delivered_at'] else '等待下一轮接收')
    for task in tasks:
        if task['session_id'] == session_id:
            add('codex:' + (task['run_id'] or task['rollout_id']), 'codex', task['started_at'],
                task['task_name'] or 'Codex 任务', '', source='Codex', status=task['status'], task=task)
    names = {'session:rename': '会话改名', 'session:archive': '归档会话', 'session:remove': '移出 Crew',
             'session:restore': '恢复会话', 'session:undo': '撤销操作', 'session:register': '加入 Crew',
             'session:join': '加入 Crew'}
    for row in conn.execute('SELECT * FROM events WHERE session_id=? ORDER BY id', (session_id,)):
        if row['kind'] in names:
            add('event:' + str(row['id']), 'event', row['ts'], names[row['kind']])
        elif row['kind'] == 'feishu:message':
            add('event:' + str(row['id']), 'event', row['ts'], '你发来的消息', json.loads(row['detail'])['text'], source='飞书')
    for row in conn.execute("SELECT * FROM events WHERE project=? AND kind LIKE 'roster:%' ORDER BY id", (s['project'],)):
        detail = json.loads(row['detail'])
        if session_id in detail.get('session_ids', []):
            add('event:' + str(row['id']), 'event', row['ts'], '退出协作组' if row['kind'] == 'roster:remove' else '加入协作组', detail['group'])
    return {'items': sorted(items, key=lambda item: (item['at'] or '', item['id']), reverse=True)}
