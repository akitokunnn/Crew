"""Workspace views composed from recorded facts, without changing the board."""


def chat_replies(events, after=0):
    """Project visible reply text, matching chat.js's stream/snapshot replay.

    Assistant events also carry tool calls. Only a change to reply text can
    advance its unread cursor; a final snapshot of streamed text is not new.
    """
    messages = {}
    for event in events:
        if event['type'] not in ('text', 'assistant'):
            continue
        state = messages.setdefault(event['id'], {'content': '', 'stream': '', 'reply': None})
        if event['type'] == 'text':
            state['stream'] += event['text']
        else:
            state['content'] = (''.join(block['text'] for block in event['content'] if block['type'] == 'text')
                                if 'content' in event else event.get('text', ''))
            state['stream'] = ''
        text = state['content'] + state['stream']
        if state['reply'] is None or state['reply']['text'] != text:
            state['reply'] = {'id': event['id'], 'seq': event['seq'], 'at': event.get('at'), 'text': text}
    return sorted((state['reply'] for state in messages.values()
                   if state['reply']['text'].strip() and state['reply']['seq'] > after), key=lambda reply: reply['seq'])


def attention(conn, sessions, decisions, tasks, journals):
    dismissed = {r[0] for r in conn.execute("SELECT item_id FROM attention_resolutions")}
    visible = {s['session_id']: s for s in sessions if s['status'] != 'closed'}
    items, asking = [], set()
    for d in decisions:
        if d['session_id'] not in visible:
            continue
        if d['status'] == 'open' or (d['status'] == 'answered' and not d['delivered_at']):
            asking.add(d['session_id'])
        if d['status'] != 'open' or d['held']:
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
