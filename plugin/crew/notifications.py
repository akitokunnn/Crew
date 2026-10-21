"""One local user's Feishu inbox, decision cards and replies.

The CLI owns authentication and its shared websocket bus. Crew records exactly
which messages it sent, and never accepts an answer for an unrelated message.
"""
import json
import re
import threading
import traceback
import uuid
from contextlib import closing
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

from . import board, feishu, store


def desktop_locked():
    token = (Path.home() / 'RemoteDev' / 'desk_token').read_text(encoding='ascii').strip()
    request = Request('http://127.0.0.1:47800/api/status', data=b'{}', method='POST',
                      headers={'Content-Type': 'application/json', 'X-Desk-Token': token})
    with build_opener(ProxyHandler({})).open(request, timeout=3) as response:
        status = json.load(response)
    if status.get('ok') is not True or type(status.get('locked')) is not bool:
        raise ValueError('桌面代理没有返回锁屏状态')
    return status['locked']


class Inbox:
    def __init__(self, service, client=None, lock_status=desktop_locked, batch_seconds=30):
        self.service, self.home = service, service.home
        self.config_path = self.home / 'feishu.json'
        self.config = json.loads(self.config_path.read_text(encoding='utf-8')) if self.config_path.exists() else {}
        self.client, self.lock_status, self.batch_seconds = client, lock_status, batch_seconds
        self.lock = threading.RLock()
        self.locked, self.error, self.presence_error = False, None, None
        self.ready, self.consumer_errors = set(), {}
        with closing(store.connect(self.home)) as conn:
            failed = conn.execute("SELECT error FROM notification_messages WHERE status IN ('error','sending') ORDER BY created_at DESC LIMIT 1").fetchone()
            uncertain_reply = conn.execute("SELECT 1 FROM notification_replies WHERE status='sending' LIMIT 1").fetchone()
        if failed or uncertain_reply:
            self.error = '上次飞书投递未完成，请检查服务日志。' + ((failed['error'] or '') if failed else '')
        self.pending, self.pending_at = {}, None
        self.stop, self.wake = threading.Event(), threading.Event()
        self.worker, self.consumers, self.readers = None, [], []

    def status(self):
        with self.lock:
            manual = self.config.get('manual_away', False)
            return dict(manual_away=manual, locked=self.locked, away=manual or self.locked,
                        enabled=self.config.get('enabled', False), card_ready=feishu.CARD_EVENT in self.ready,
                        error=self.error or '；'.join(self.consumer_errors.values()) or self.presence_error)

    def set_away(self, away):
        with self.lock:
            self.config['manual_away'] = away
            self.config_path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding='utf-8')
        self.wake.set()
        return self.status()

    def start(self):
        if not self.config.get('enabled'):
            return
        self.worker = threading.Thread(target=self.run, name='crew-feishu', daemon=True)
        self.worker.start()

    def run(self):
        try:
            self.client = self.client or feishu.Client()
            for key in feishu.EVENT_KEYS:
                process = self.client.start_consumer(key)
                self.consumers.append(process)
                for pipe, target, args in ((process.stdout, self._read_events, (process.stdout,)),
                                           (process.stderr, self._read_errors, (process.stderr, key))):
                    thread = threading.Thread(target=target, args=args, daemon=True)
                    thread.start()
                    self.readers.append(thread)
            while not self.stop.is_set():
                self.tick()
                self.wake.wait(2)
                self.wake.clear()
        except Exception as exc:
            self.error = str(exc)
            traceback.print_exc()
        finally:
            for process in self.consumers:
                if process.poll() is None:
                    process.stdin.close()
            for process in self.consumers:
                try:
                    process.wait(timeout=5)
                except Exception as exc:
                    self.error = '飞书接收进程未正常结束：' + str(exc)

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.worker:
            self.worker.join(timeout=12)

    def _read_events(self, stream):
        for line in stream:
            try:
                self.receive(json.loads(line))
            except Exception as exc:
                self.error = '飞书消息处理失败：' + str(exc)
                traceback.print_exc()

    def _read_errors(self, stream, key):
        lines = []
        for line in stream:
            if '[event] ready' in line:
                self.ready.add(key)
                continue
            lines.append(line)
        self.ready.discard(key)
        if self.stop.is_set():
            return
        try:
            envelope = json.loads(''.join(lines))
            message = envelope['error']['message']
        except (ValueError, KeyError, TypeError):
            message = '接收连接已停止，请查看服务日志'
        if key == feishu.CARD_EVENT and 'not subscribed' in message:
            message = '飞书尚未启用卡片按钮回调，当前请用文字答复卡片。醒后需在开放平台启用 card.action.trigger。'
        self.consumer_errors[key] = message

    def _leader(self, conn, session_id):
        session = board.session(conn, session_id)
        if session['role'] == 'leader' or not session['group_name']:
            return session
        group = conn.execute('SELECT leader_session_id FROM groups WHERE project=? AND name=?',
                             (session['project'], session['group_name'])).fetchone()
        return board.session(conn, group[0]) if group and group[0] else session

    def tick(self):
        if not self.config.get('manual_away'):
            try:
                self.locked = self.lock_status()
                self.presence_error = None
            except (OSError, URLError, ValueError) as exc:
                self.locked = False
                self.presence_error = '无法读取锁屏状态，请用“我已离开”开关：' + str(exc)
        self.process_replies()
        if not self.status()['away']:
            return
        now = self.service.now()
        with closing(store.connect(self.home)) as conn:
            sent = {row[0] for row in conn.execute('SELECT item_id FROM notification_messages')}
            projects = board.projects(conn)
        active = set()
        for project in projects:
            state = self.service.state(project['name'])
            for item in state['attention']:
                if item['kind'] != 'decision' and (item['created_at'] or '') < self.config.get('since', ''):
                    continue
                if item['id'] not in sent and item['session_id']:
                    active.add(item['id'])
                    self.pending[item['id']] = {**item, 'project': project['name']}
            with closing(store.connect(self.home)) as conn:
                reports = board.reports(conn, project['name'], since=self.config.get('since'))
            for report in reports:
                identity = 'report:' + str(report['id'])
                if report['created_at'] < self.config.get('since', '') or identity in sent:
                    continue
                with closing(store.connect(self.home)) as conn:
                    session = board.session(conn, report['session_id'])
                if session['role'] == 'leader' and session['status'] != 'closed':
                    active.add(identity)
                    self.pending[identity] = dict(id=identity, kind='report', project=project['name'],
                        session_id=report['session_id'], title='阶段进展', detail=report['body'], created_at=report['created_at'])
        self.pending = {key: item for key, item in self.pending.items() if key in active}
        if not self.pending:
            self.pending_at = None
        else:
            self.pending_at = self.pending_at or now
            if (now - self.pending_at).total_seconds() >= self.batch_seconds:
                self.flush()
                self.pending.clear()
                self.pending_at = None

    def flush(self):
        entries, notes = [], []
        now = self.service.now().isoformat()
        with closing(store.connect(self.home)) as conn:
            for item in self.pending.values():
                leader = self._leader(conn, item['session_id'])
                label = leader['crew_name'] or '未命名会话'
                decision = next((d for d in board.decisions(conn, item['project'], all=True)
                                 if d['id'] == item.get('decision_id') and d['status'] == 'open'), None)
                if item['kind'] == 'decision' and decision is None:
                    continue
                entry = dict(item=item, decision=decision, workspace=item['project'], leader=label,
                             leader_session_id=leader['session_id'], buttons_enabled=feishu.CARD_EVENT in self.ready)
                if decision is None:
                    entry['text'] = item['title'] + '\n' + item['detail']
                (entries if decision else notes).append(entry)
                conn.execute('INSERT INTO notification_messages(item_id,project,leader_session_id,decision_id,kind,created_at,content,status) VALUES(?,?,?,?,?,?,?,?)',
                             (item['id'], item['project'], leader['session_id'], item.get('decision_id'), item['kind'], now,
                              json.dumps(entry, ensure_ascii=False), 'sending'))
            batches = [(entries + notes, True)] if entries else [(notes, False)]
            for batch, is_card in batches:
                if not batch:
                    continue
                key = str(uuid.uuid5(uuid.NAMESPACE_URL, '|'.join(sorted(e['item']['id'] for e in batch))))
                try:
                    if is_card:
                        result = self.client.send_questions(self.config['owner_id'], batch, idempotency_key=key)
                    else:
                        text = '\n\n'.join('%s · %s\n%s\n%s' % (e['workspace'], e['leader'], e['item']['title'], e['item']['detail']) for e in batch)
                        result = self.client.send_text(self.config['owner_id'], text, idempotency_key=key)
                    for entry in batch:
                        conn.execute("UPDATE notification_messages SET message_id=?,chat_id=?,status='sent' WHERE item_id=?",
                                     (result['message_id'], result.get('chat_id'), entry['item']['id']))
                except Exception as exc:
                    for entry in batch:
                        conn.execute("UPDATE notification_messages SET status='error',error=? WHERE item_id=?", (str(exc), entry['item']['id']))
                    raise

    def refresh_card(self, conn, message_id):
        entries = []
        for row in conn.execute('SELECT * FROM notification_messages WHERE message_id=?', (message_id,)):
            entry = json.loads(row['content'])
            if row['decision_id'] is not None:
                entry['decision'] = next(d for d in board.decisions(conn, row['project'], all=True) if d['id'] == row['decision_id'])
            entries.append(entry)
        if entries:
            self.client.update_questions(message_id, entries)

    def receive(self, event):
        owner = self.config['owner_id']
        action = feishu.parse_card_action(event, owner_id=owner)
        reply = feishu.parse_text_reply(event, owner_id=owner)
        if action is None and reply is None:
            return self.receive_text(event)
        identity = action['event_id'] if action else reply['message_id']
        message_id = action['message_id'] if action else reply['reply_to']
        with self.service.write_lock, closing(store.connect(self.home)) as conn:
            if conn.execute('SELECT 1 FROM notification_inbound WHERE event_id=?', (identity,)).fetchone():
                return
            rows = list(conn.execute("SELECT * FROM notification_messages WHERE message_id=? AND status='sent'", (message_id,)))
            if action:
                rows = [r for r in rows if r['project'] == action['workspace'] and r['decision_id'] == action['decision_id']
                        and json.loads(r['content'])['leader'] == action['leader']]
            questions = [r for r in rows if r['decision_id'] is not None]
            if not rows:
                return None if action else self.queue_text(conn, reply, None)
            if not action:
                open_ids = {d['id'] for r in rows for d in board.decisions(conn, r['project'])}
                questions = [r for r in questions if r['decision_id'] in open_ids]
            if not action:
                numbered = re.fullmatch(r'#(\d+)\s+(.+)', reply['answer'], flags=re.S)
                if numbered:
                    questions = [row for row in questions if row['decision_id'] == int(numbered[1])]
                    reply['answer'] = numbered[2]
                if (numbered and len(questions) != 1) or (not numbered and len(questions) > 1):
                    self.client.send_text(owner, '这张卡有多条问题，请点对应选项，或回复“#编号 答复”，例如“#12 同意”。', idempotency_key=identity)
                    conn.execute('INSERT INTO notification_inbound VALUES(?,?)', (identity, self.service.now().isoformat()))
                    return
            if not action and not questions:
                return self.queue_text(conn, reply, rows[0]['leader_session_id'] if len({r['leader_session_id'] for r in rows}) == 1 else None)
            row = questions[0]
            decision = next(d for d in board.decisions(conn, row['project'], all=True) if d['id'] == row['decision_id'])
            answer = action['answer'] if action else reply['answer']
            if board.session(conn, decision['session_id'])['status'] == 'closed':
                self.client.send_text(owner, '这条会话已归档或移出 Crew，答复尚未保存。请先在 Crew 恢复会话。', idempotency_key=identity)
                conn.execute('INSERT INTO notification_inbound VALUES(?,?)', (identity, self.service.now().isoformat()))
                return
            if action and answer not in decision['options']:
                raise board.BoardError('飞书按钮不属于这条问题的选项')
            if decision['status'] == 'open':
                board.answer(conn, decision['id'], answer, actor='feishu', now=self.service.now().isoformat())
            conn.execute('INSERT INTO notification_inbound VALUES(?,?)', (identity, self.service.now().isoformat()))
            self.refresh_card(conn, message_id)

    def receive_text(self, event):
        if (event.get('type') != feishu.MESSAGE_EVENT or event.get('sender_id') != self.config['owner_id']
                or event.get('sender_type') != 'user' or event.get('chat_type') != 'p2p'
                or event.get('message_type') != 'text' or not isinstance(event.get('content'), str)):
            return
        with self.service.write_lock, closing(store.connect(self.home)) as conn:
            self.queue_text(conn, dict(message_id=event['message_id'], answer=event['content'], chat_id=event['chat_id']), None)

    def queue_text(self, conn, reply, session_id):
        identity, text = reply['message_id'], reply['answer']
        if conn.execute('SELECT 1 FROM notification_inbound WHERE event_id=?', (identity,)).fetchone():
            return
        leaders = [dict(row) for row in conn.execute("SELECT s.* FROM sessions s LEFT JOIN session_controls c ON s.session_id=c.session_id WHERE role='leader' AND status!='closed' AND COALESCE(c.removed,0)=0")]
        prefix = re.match(r'^@([^\s]+)\s+(.+)', text, flags=re.S)
        if prefix:
            selected = [s for s in leaders if prefix[1] in (s['crew_name'], s['project'] + '/' + s['crew_name'])]
            session_id, text = (selected[0]['session_id'], prefix[2]) if len(selected) == 1 else (None, text)
        elif session_id is None and len(leaders) == 1:
            session_id = leaders[0]['session_id']
        if session_id is None:
            choices = '\n'.join('@%s/%s 你的消息' % (s['project'], s['crew_name']) for s in leaders)
            self.client.send_text(self.config['owner_id'], '请指定要找的 leader，或直接回复它发来的消息。\n' + (choices or '目前没有未归档的 leader，请先在 Crew 组建协作组。'), idempotency_key=identity)
        else:
            conn.execute('INSERT INTO notification_replies VALUES(?,?,?,?,?,?)', (identity, session_id, text, 'queued', None, self.service.now().isoformat()))
        conn.execute('INSERT INTO notification_inbound VALUES(?,?)', (identity, self.service.now().isoformat()))
        self.wake.set()

    def process_replies(self):
        with self.service.write_lock, closing(store.connect(self.home)) as conn:
            for row in conn.execute("SELECT * FROM notification_replies WHERE status IN ('queued','running') ORDER BY created_at").fetchall():
                session = board.session(conn, row['session_id'])
                sid = session['session_id']
                label = '%s · %s' % (session['project'], session['crew_name'] or '会话')
                journal = self.service.chat.journal(sid)
                if row['status'] == 'queued':
                    if session['status'] == 'closed':
                        self._reply(conn, row, label + '\n这条会话已归档，消息未发送。', 'error')
                        continue
                    if self.service.chat.busy(sid):
                        continue
                    if conn.execute("SELECT 1 FROM notification_replies WHERE session_id=? AND status='running'", (sid,)).fetchone():
                        continue
                    # Web leaders have an owned input stream (or a saved resume spec).
                    spec = self.home / 'spawn' / ((session['spawn_id'] or '') + '.json')
                    with self.service.agents_lock:
                        external = any(a['sessionId'] == sid for a in self.service.agents) and not self.service.chat.is_owned(sid)
                    if external or not spec.exists():
                        board.handoff(conn, sid, session['crew_name'], '来自飞书：\n' + row['text'], now=self.service.now().isoformat())
                        board.record_event(conn, 'feishu:message', session['project'], sid, {'text': row['text']}, now=self.service.now().isoformat())
                        self._reply(conn, row, label + '\n消息已写入交接。这条会话在 VS Code 或终端中，会在下一轮收到；可在网页查看交接。', 'handed_off')
                        continue
                    after = len(journal.events)
                    try:
                        self.service.chat.send(session, row['text'], source='feishu', identity='feishu:' + row['message_id'])
                    except (OSError, board.BoardError) as exc:
                        self._reply(conn, row, label + '\n消息未发送：' + str(exc), 'error')
                        continue
                    conn.execute("UPDATE notification_replies SET status='running',after_seq=? WHERE message_id=?", (after, row['message_id']))
                elif self.status()['away']:
                    events = journal.after(row['after_seq'])
                    end = next((i for i, e in enumerate(events) if e['type'] == 'status' and e['status'] in ('idle', 'offline')), None)
                    if end is None and self.service.chat.running(sid):
                        continue
                    events = events[:end + 1] if end is not None else events
                    error = next((e['text'] for e in reversed(events) if e['type'] == 'error'), None)
                    assistant = next((e for e in reversed(events) if e['type'] == 'assistant'), None)
                    text = '\n'.join(b['text'] for b in assistant['content'] if b['type'] == 'text') if assistant else ''
                    if error or text:
                        self._reply(conn, row, label + '\n' + (text or '会话出错：' + error), 'answered')
                    elif not self.service.chat.running(sid):
                        self._reply(conn, row, label + '\n会话已停止，还没有收到这条消息的回复。请在 Crew 查看会话。', 'error')

    def _reply(self, conn, row, text, status):
        identity = 'reply:' + row['message_id']
        # A persisted sending state surfaces uncertain delivery instead of sending twice.
        conn.execute("UPDATE notification_replies SET status='sending' WHERE message_id=?", (row['message_id'],))
        result = self.client.send_text(self.config['owner_id'], text, idempotency_key=str(uuid.uuid5(uuid.NAMESPACE_URL, identity)))
        session = board.session(conn, row['session_id'])
        conn.execute('INSERT INTO notification_messages VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                     (identity, session['project'], session['session_id'], None, 'reply', result['message_id'], result.get('chat_id'),
                      self.service.now().isoformat(), '{}', 'sent', None))
        conn.execute('UPDATE notification_replies SET status=? WHERE message_id=?', (status, row['message_id']))
