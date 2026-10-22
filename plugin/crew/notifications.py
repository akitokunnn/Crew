"""One local user's Feishu inbox, decision cards and replies.

The CLI owns authentication and its shared websocket bus. Crew records which
questions it has shown before accepting a numbered answer.
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

HELP = ('/h 帮助\n/ls 所有 leader（带编号）\n/1 切换到编号 1 的 leader，/2 以此类推\n'
        '/q 所有项目的待拍板\n/back 我回来了\n'
        '普通文字发给当前 leader。/1 是 leader 编号；#42 答复 是拍板题号，无需引用消息。')


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
        self.config.setdefault('current_leader_session_id', None)
        self.config.setdefault('leader_numbers', {})
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
            self.save_config()
        self.wake.set()
        return self.status()

    def save_config(self):
        # A rename-into-place collides with Windows scanners holding the fresh temp file; the file is tiny.
        with self.lock:
            self.config_path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding='utf-8')

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
            message = '飞书卡片按钮尚未启用，请用文字答复；需在开放平台启用 card.action.trigger。'
        self.consumer_errors[key] = message

    def _leader(self, conn, session_id):
        session = board.session(conn, session_id)
        if session['role'] == 'leader' or not session['group_name']:
            return session
        group = conn.execute('SELECT leader_session_id FROM groups WHERE project=? AND name=?',
                             (session['project'], session['group_name'])).fetchone()
        return board.session(conn, group[0]) if group and group[0] else session

    def refresh_presence(self):
        if not self.config.get('manual_away'):
            try:
                self.locked = self.lock_status()
                self.presence_error = None
            except (OSError, URLError, ValueError) as exc:
                self.locked = False
                self.presence_error = '无法读取锁屏状态，请用“我已离开”开关：' + str(exc)

    def tick(self):
        self.refresh_presence()
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
                        text = '\n\n'.join('%s·%s\n%s\n%s' % (e['workspace'], e['leader'], e['item']['title'], e['item']['detail']) for e in batch)
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
        reply = None if action else feishu.parse_private_text(event, owner_id=owner)
        if action is None and reply is None:
            return
        identity = action['event_id'] if action else reply['message_id']
        with self.service.write_lock, closing(store.connect(self.home)) as conn:
            if conn.execute('SELECT 1 FROM notification_inbound WHERE event_id=?', (identity,)).fetchone():
                return
            if action:
                rows = [r for r in conn.execute("SELECT * FROM notification_messages WHERE message_id=? AND status='sent' AND kind='decision'", (action['message_id'],))
                        if r['project'] == action['workspace'] and r['decision_id'] == action['decision_id']
                        and json.loads(r['content'])['leader'] == action['leader']]
                if not rows:
                    return
                decision = next(d for d in board.decisions(conn, rows[0]['project'], all=True) if d['id'] == action['decision_id'])
                if action['answer'] not in decision['options']:
                    raise board.BoardError('飞书按钮不属于这条问题的选项')
                self.save_answer(conn, decision, action['answer'], identity)
            elif reply['answer'].startswith('/'):
                self.handle_command(conn, reply)
            else:
                numbered = re.fullmatch(r'#(\d+)\s+(.+)', reply['answer'], flags=re.S)
                if numbered and numbered[2].strip():
                    self.answer_numbered(conn, int(numbered[1]), numbered[2], identity)
                else:
                    self.queue_text(conn, reply)
            conn.execute('INSERT INTO notification_inbound VALUES(?,?)', (identity, self.service.now().isoformat()))
        self.wake.set()

    def leader_catalog(self, conn):
        leaders = [dict(row) for row in conn.execute(
            "SELECT s.* FROM sessions s JOIN projects p ON p.name=s.project "
            "LEFT JOIN session_controls c ON c.session_id=s.session_id "
            "WHERE s.role='leader' AND s.status!='closed' AND COALESCE(c.removed,0)=0 "
            "AND c.archived_status IS NULL ORDER BY p.created_at,p.name,s.started_at,s.session_id")]
        with self.lock:
            numbers = self.config['leader_numbers']
            next_number = max(numbers.values(), default=0) + 1
            changed = False
            for leader in leaders:
                sid = leader['session_id']
                if sid not in numbers:
                    numbers[sid] = next_number
                    next_number += 1
                    changed = True
                leader['number'] = numbers[sid]
            if changed:
                self.save_config()
        return sorted(leaders, key=lambda s: s['number'])

    def handle_command(self, conn, reply):
        command, identity = reply['answer'], reply['message_id']
        if command == '/h':
            text = HELP
        elif command == '/back':
            self.set_away(False)
            self.refresh_presence()
            if self.presence_error is not None:
                text = '已取消手动离开；当前无法确认是否仍因锁屏被视为离开。' + self.presence_error
            else:
                text = '已取消手动离开；当前仍因锁屏被视为离开。' if self.locked else '已取消手动离开；当前未因锁屏被视为离开。'
        elif command == '/q':
            return self.show_questions(conn, identity)
        elif command == '/ls':
            leaders = self.leader_catalog(conn)
            states = {}
            with self.service.agents_lock:
                agents, error = self.service.agents, self.service.agents_error
            for project in {s['project'] for s in leaders}:
                identities = [{'sessionId': s['session_id']} for s in board.sessions(conn, project)] if error else agents
                state = board.status(conn, project, identities)
                states.update((s['session_id'], s['status']) for s in [*state['ungrouped'], *(s for g in state['groups'] for s in g['members'])])
            pending = {}
            for decision in conn.execute("SELECT session_id FROM decisions WHERE status='open'"):
                asker = board.session(conn, decision['session_id'])
                leader = asker if asker['role'] == 'leader' else board.leader_of(conn, asker['session_id'])
                if leader is not None:
                    sid = leader['session_id']
                    pending[sid] = pending.get(sid, 0) + 1
            lines = []
            for leader in leaders:
                sid = leader['session_id']
                journal = self.service.chat.journal(sid)
                status = states[sid]
                if self.service.chat.running(sid) and (status != 'waiting' or journal.status == 'working'):
                    status = journal.status
                unread = len({e['id'] for e in journal.after(board.read_seq(conn, sid)) if e['type'] == 'assistant'})
                current = '（当前）' if sid == self.config['current_leader_session_id'] else ''
                lines.append('%s %s·%s%s %s 未读 %s 待拍板 %s' % (
                    leader['number'], leader['project'], leader['crew_name'], current, status, unread, pending.get(sid, 0)))
            text = '\n'.join(lines) if lines else '目前没有有效的 leader。'
        else:
            numbered = re.fullmatch(r'/([1-9][0-9]*)', command)
            if numbered:
                selected = next((s for s in self.leader_catalog(conn) if s['number'] == int(numbered[1])), None)
                if selected is None:
                    text = '这个 leader 编号已失效，请先 /ls。'
                else:
                    with self.lock:
                        self.config['current_leader_session_id'] = selected['session_id']
                        self.save_config()
                    text = '已切换到 %s·%s。' % (selected['project'], selected['crew_name'])
            else:
                text = '未知命令，请用 /h 查看帮助。'
        self.client.send_text(self.config['owner_id'], text, idempotency_key=identity)

    def show_questions(self, conn, identity):
        grouped, entries = {}, []
        for project in board.projects(conn):
            for decision in board.decisions(conn, project['name']):
                asker = board.session(conn, decision['session_id'])
                leader = asker if asker['role'] == 'leader' else board.leader_of(conn, asker['session_id'])
                label = leader['crew_name'] if leader is not None else '无leader'
                grouped.setdefault((project['name'], label), []).append(decision)
                entries.append(dict(decision=decision, workspace=project['name'], leader=label,
                                    leader_session_id=leader['session_id'] if leader is not None else asker['session_id']))
        lines = []
        for (project, leader), decisions in grouped.items():
            lines.append('%s·%s' % (project, leader))
            for decision in decisions:
                asker = board.session(conn, decision['session_id'])
                lines.append('#%s 提问者：%s\n问题：%s\n推荐：%s' % (
                    decision['id'], asker['crew_name'], decision['question'],
                    decision['recommendation'] if decision['recommendation'] is not None else '无'))
        result = self.client.send_text(self.config['owner_id'], '\n\n'.join(lines) if lines else '当前没有未答拍板。', idempotency_key=identity)
        for entry in entries:
            conn.execute('INSERT INTO notification_messages(item_id,project,leader_session_id,decision_id,kind,message_id,chat_id,created_at,content,status) VALUES(?,?,?,?,?,?,?,?,?,?)',
                         ('query:%s:%s' % (identity, entry['decision']['id']), entry['workspace'], entry['leader_session_id'],
                          entry['decision']['id'], 'query', result['message_id'], result.get('chat_id'), self.service.now().isoformat(),
                          json.dumps(entry, ensure_ascii=False), 'sent'))

    def answer_numbered(self, conn, decision_id, answer, identity):
        shown = conn.execute("SELECT project FROM notification_messages WHERE decision_id=? AND status='sent' AND kind IN ('decision','query') LIMIT 1", (decision_id,)).fetchone()
        if shown is None:
            self.client.send_text(self.config['owner_id'], '这道题尚未展示，请先 /q 查看待拍板。', idempotency_key=identity)
            return
        decision = next(d for d in board.decisions(conn, shown['project'], all=True) if d['id'] == decision_id)
        self.save_answer(conn, decision, answer, identity)

    def save_answer(self, conn, decision, answer, identity):
        if decision['status'] != 'open':
            self.client.send_text(self.config['owner_id'], '这道题已处理，请先 /q 查看待拍板。', idempotency_key=identity)
            return
        asker = board.session(conn, decision['session_id'])
        if asker['status'] == 'closed' or board.is_archived(conn, asker['session_id']):
            self.client.send_text(self.config['owner_id'], '这条会话已归档或移出 Crew，答复尚未保存。请先在 Crew 恢复会话。', idempotency_key=identity)
            return
        now = self.service.now().isoformat()
        board.answer(conn, decision['id'], answer, actor='feishu', now=now)
        leader = asker if asker['role'] == 'leader' else board.leader_of(conn, asker['session_id'])
        if leader is not None:
            notice = '#%s\n提问者：%s（cc_name：%s）\n问题：%s\n答复：%s\n请马上用 SendMessage 转告并叫它继续' % (
                decision['id'], asker['crew_name'], asker['cc_name'], decision['question'], answer)
            board.queue_leader_notice(conn, leader['session_id'], notice, 'feishu', now=now)
        cards = conn.execute("SELECT DISTINCT message_id FROM notification_messages WHERE decision_id=? AND status='sent' AND kind='decision'", (decision['id'],)).fetchall()
        for card in cards:
            self.refresh_card(conn, card['message_id'])
        self.client.send_text(self.config['owner_id'], '#%s 答复已保存。' % decision['id'], idempotency_key=identity)

    def queue_text(self, conn, reply):
        session_id = self.config['current_leader_session_id']
        leaders = self.leader_catalog(conn)
        if session_id not in {s['session_id'] for s in leaders}:
            self.client.send_text(self.config['owner_id'], '先用 /ls 和 /1 选一个 leader。', idempotency_key=reply['message_id'])
            return
        conn.execute('INSERT INTO notification_replies VALUES(?,?,?,?,?,?)',
                     (reply['message_id'], session_id, reply['answer'], 'queued', None, self.service.now().isoformat()))

    def process_replies(self):
        with self.service.write_lock, closing(store.connect(self.home)) as conn:
            for row in conn.execute("SELECT * FROM notification_replies WHERE status IN ('queued','running') ORDER BY created_at,rowid").fetchall():
                session = board.session(conn, row['session_id'])
                sid = session['session_id']
                label = '%s·%s' % (session['project'], session['crew_name'])
                journal = self.service.chat.journal(sid)
                if row['status'] == 'queued':
                    if session['role'] != 'leader':
                        self._reply(conn, row, label + '\n这个会话已不是 leader，消息未发送。', 'error')
                        continue
                    if session['status'] == 'closed' or board.is_archived(conn, sid):
                        self._reply(conn, row, label + '\n这条会话已归档，消息未发送。', 'error')
                        continue
                    if self.service.chat.busy(sid):
                        continue
                    if conn.execute("SELECT 1 FROM notification_replies WHERE session_id=? AND status='running'", (sid,)).fetchone():
                        continue
                    # A saved web journal/spec can resume; external processes cannot receive our input.
                    spec = self.home / 'spawn' / ((session['spawn_id'] or '') + '.json')
                    with self.service.agents_lock:
                        external = any(a['sessionId'] == sid for a in self.service.agents) and not self.service.chat.is_owned(sid)
                    if external or not self.service.chat.path(sid).exists() or not spec.exists():
                        self._reply(conn, row, label + '\n这个 leader 不在网页后台运行，收不到飞书消息', 'error')
                        continue
                    after = len(journal.events)
                    try:
                        self.service.chat.send(session, row['text'], source='feishu', identity='feishu:' + row['message_id'])
                    except (OSError, board.BoardError) as exc:
                        self._reply(conn, row, label + '\n消息未发送：' + str(exc), 'error')
                        continue
                    conn.execute("UPDATE notification_replies SET status='running',after_seq=? WHERE message_id=?", (after, row['message_id']))
                else:
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
                    elif end is not None and events[-1]['status'] == 'idle':
                        self._reply(conn, row, label + '\n（这一轮没有文字回复）', 'answered')
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
