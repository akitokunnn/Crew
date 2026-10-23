"""One owner's QQ inbox, decision buttons and queued leader conversations."""
import base64
import json
import logging
import re
import threading
import uuid
from contextlib import closing
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

from . import board, chat, qq, store

TEXT_LIMIT = 2000  # 10-23 实测 5000 字也能发；2000 一段是为了手机上好读。
LOG = logging.getLogger(__name__)

HELP = ('/help（/h）帮助\n/leader（/l）所有 leader（带编号）\n'
        '/leader 2（/l 2、/2）切换到编号 2 的 leader，/1 以此类推\n'
        '/decisions（/d）所有项目未挂起的待拍板\n/back（/b）我回来了\n'
        '普通文字和图片发给当前 leader。/1 是 leader 编号；点按钮或引用拍板消息回复。')


# CommonMark ignores ** that touches punctuation on its inner side and a letter on its outer side, which is
# exactly how Chinese writes "**标题：**正文"; QQ and the web both left the asterisks visible (user 10-23).
BOLD_PUNCT = r'‐-‧　-〿！-／：-＠［-｀｛-･.,:;!?()\[\]"\''
BOLD_TAIL = re.compile(r'\*\*([^*\n]+?)([%s]+)\*\*(?=[^\s*%s])' % (BOLD_PUNCT, BOLD_PUNCT))
BOLD_HEAD = re.compile(r'(?<=[^\s*%s])\*\*([%s]+)([^*\n]+?)\*\*' % (BOLD_PUNCT, BOLD_PUNCT))


def cjk_bold(text):
    """Move punctuation just inside ** to the outside when it would otherwise stop the bold from rendering."""
    return BOLD_HEAD.sub(r'\1**\2**', BOLD_TAIL.sub(r'**\1**\2', text))


def leader_parts(project, name, text):
    """Leader words as QQ Markdown: a small heading with the name, a rule, then the body (user 10-23 chose this)."""
    for number, part in enumerate(text_parts(text)):
        yield '### %s · %s%s\n***\n%s' % (name, project, '（续）' if number else '', cjk_bold(part))


def text_parts(text):
    while len(text) > TEXT_LIMIT:
        end = text.rfind('\n\n', 0, TEXT_LIMIT + 1)
        end = end + 2 if end > 0 and end + 2 <= TEXT_LIMIT else TEXT_LIMIT
        yield text[:end]
        text = text[end:]
    if text:
        yield text


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
        credentials_path = self.home / 'qq.json'
        self.credentials = None
        if credentials_path.exists():
            credentials = json.loads(credentials_path.read_text(encoding='utf-8'))
            self.credentials = (credentials['app_id'], credentials['app_secret'])
        self.config_path = self.home / 'notify.json'
        self.config = (json.loads(self.config_path.read_text(encoding='utf-8')) if self.config_path.exists()
                       else dict(owner_openid=None, since=None, manual_away=False,
                                 current_leader_session_id=None, leader_numbers={}))
        self.client, self.lock_status, self.batch_seconds = client, lock_status, batch_seconds
        self.lock = threading.RLock()
        self.locked, self.error, self.presence_error = False, None, None
        self.connection_state = 'connecting'
        self.channel = dict(state=('unconfigured' if self.credentials is None else
                                   'unbound' if self.config['owner_openid'] is None else 'connecting'), detail=None)
        with closing(store.connect(self.home)) as conn:
            failed = conn.execute("SELECT error FROM notification_messages WHERE status IN ('error','sending') ORDER BY created_at DESC LIMIT 1").fetchone()
            uncertain_reply = conn.execute("SELECT 1 FROM notification_replies WHERE status='sending' LIMIT 1").fetchone()
        if failed or uncertain_reply:
            self.error = '上次 QQ 投递未完成，请检查服务日志。' + ((failed['error'] or '') if failed else '')
        self.pending, self.pending_at = {}, None
        # Switching "我已离开" on by hand means the user has gone; only lock-screen absence waits out the batch window.
        self.push_now = False
        # Journal position per leader when the user left; None while present.
        self.relay_after = None
        self.stop, self.wake = threading.Event(), threading.Event()
        self.worker, self.receiver = None, None

    def status(self):
        with self.lock:
            manual = self.config['manual_away']
            return dict(manual_away=manual, locked=self.locked, away=manual or self.locked,
                        enabled=self.credentials is not None, channel=self.channel.copy(),
                        error=self.error or self.presence_error)

    def set_away(self, away):
        with self.lock:
            self.config['manual_away'] = away
            self.save_config()
            self.push_now = away
        self.wake.set()
        return self.status()

    def save_config(self):
        # A rename-into-place collides with Windows scanners holding the fresh temp file; the file is tiny.
        with self.lock:
            self.config_path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding='utf-8')

    def start(self):
        if self.credentials is None:
            return
        self.client = qq.Client(*self.credentials) if self.client is None else self.client
        self.receiver = threading.Thread(target=self.receive_loop, name='crew-qq-receive', daemon=True)
        self.worker = threading.Thread(target=self.run, name='crew-qq-notify', daemon=True)
        self.receiver.start()
        self.worker.start()

    def run(self):
        while not self.stop.is_set():
            try:
                self.tick()
            except (qq.QQError, OSError, board.BoardError) as exc:
                self.error = '%s: %s' % (type(exc).__name__, exc)
                LOG.exception('QQ notification processing failed')
            self.wake.wait(2)
            self.wake.clear()

    def receive_loop(self):
        try:
            self.client.run(self.receive, self.on_state, self.stop)
        except qq.QQError as exc:
            self.stop.set()
            self.fail_channel(exc)
            self.wake.set()

    def on_state(self, state, detail):
        with self.lock:
            self.connection_state = state
            if state == 'stopped':
                if not self.stop.is_set():
                    self.channel = dict(state='error', detail=detail or 'QQ 接收连接已停止')
            elif self.channel['state'] not in ('rejected', 'error'):
                self.channel = dict(state=('unbound' if state == 'connected' and
                                          self.config['owner_openid'] is None else state), detail=detail)

    def fail_channel(self, exc):
        reason = '%s(%s): %s' % (type(exc).__name__, exc.kind, exc)
        with self.lock:
            self.channel = dict(state='rejected' if exc.kind == 'rejected' else 'error',
                                detail=(reason + '；请在手机 QQ 打开“允许主动发送”'
                                        if exc.kind == 'rejected' else reason))
            self.error = reason

    def send(self, method, *args):
        try:
            result = method(self.config['owner_openid'], *args)
        except qq.QQError as exc:
            self.fail_channel(exc)
            raise
        with self.lock:
            if not self.stop.is_set() and self.channel['state'] in ('rejected', 'error', 'unbound', 'connecting'):
                self.channel = dict(state='reconnecting' if self.connection_state == 'reconnecting' else 'connected', detail=None)
                self.error = None
        return result

    def send_text(self, text):
        return self.send(self.client.send_text, text)

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.worker:
            self.worker.join(timeout=12)
        if self.receiver:
            self.receiver.join(timeout=12)

    def _leader(self, conn, session_id):
        session = board.session(conn, session_id)
        if session['role'] == 'leader' or not session['group_name']:
            return session
        group = conn.execute('SELECT leader_session_id FROM groups WHERE project=? AND name=?',
                             (session['project'], session['group_name'])).fetchone()
        return board.session(conn, group[0]) if group and group[0] else session

    def refresh_presence(self):
        if not self.config['manual_away']:
            try:
                self.locked = self.lock_status()
                self.presence_error = None
            except (OSError, URLError, ValueError) as exc:
                self.locked = False
                self.presence_error = '无法读取锁屏状态，请用“我已离开”开关：%s: %s' % (type(exc).__name__, exc)

    def tick(self):
        if self.config['owner_openid'] is None:
            return
        self.refresh_presence()
        self.process_replies()
        if not self.status()['away']:
            self.relay_after = None
            return
        self.relay_turns()
        now = self.service.now()
        with closing(store.connect(self.home)) as conn:
            sent = {row[0] for row in conn.execute('SELECT item_id FROM notification_messages')}
            projects = board.projects(conn)
        active = set()
        for project in projects:
            state = self.service.state(project['name'])
            for item in state['attention']:
                if item['kind'] != 'decision' and (item['created_at'] or '') < self.config['since']:
                    continue
                if item['id'] not in sent and item['session_id']:
                    active.add(item['id'])
                    self.pending[item['id']] = {**item, 'project': project['name']}
            with closing(store.connect(self.home)) as conn:
                reports = board.reports(conn, project['name'], since=self.config['since'])
            for report in reports:
                identity = 'report:' + str(report['id'])
                if report['created_at'] < self.config['since'] or identity in sent:
                    continue
                with closing(store.connect(self.home)) as conn:
                    session = board.session(conn, report['session_id'])
                if session['role'] == 'leader' and session['status'] != 'closed':
                    active.add(identity)
                    self.pending[identity] = dict(id=identity, kind='report', project=project['name'],
                        session_id=report['session_id'], title='阶段进展', detail=report['body'], created_at=report['created_at'])
        self.pending = {key: item for key, item in self.pending.items() if key in active}
        push_now, self.push_now = self.push_now, False
        if not self.pending:
            self.pending_at = None
        else:
            self.pending_at = self.pending_at or now
            if push_now or (now - self.pending_at).total_seconds() >= self.batch_seconds:
                self.flush()
                self.pending.clear()
                self.pending_at = None

    def relay_turns(self):
        """While the user is away, send each leader's closing words of every finished turn to QQ."""
        with self.service.write_lock, closing(store.connect(self.home)) as conn:
            leaders = [s for s in self.leader_catalog(conn) if self.service.chat.path(s['session_id']).exists()]
            if self.relay_after is None:
                # Turns that ended before the user left were already seen on the web.
                self.relay_after = {s['session_id']: len(self.service.chat.journal(s['session_id']).events) for s in leaders}
                return
            for leader in leaders:
                sid = leader['session_id']
                events = self.service.chat.journal(sid).after(self.relay_after.setdefault(sid, 0))
                start = 0
                for index, event in enumerate(events):
                    if event['type'] != 'status' or event['status'] != 'idle':
                        continue
                    turn, start = events[start:index], index + 1
                    self.relay_after[sid] = event['seq']
                    opener = next((e for e in turn if e['type'] == 'message'), None)
                    closing_words = next((e for e in reversed(turn) if e['type'] == 'assistant'), None)
                    # A turn started from QQ is answered by process_replies already.
                    if closing_words is None or opener is not None and opener['source'] == 'qq':
                        continue
                    text = '\n'.join(b['text'] for b in closing_words['content'] if b['type'] == 'text')
                    identity = 'turn:%s:%s' % (sid, event['seq'])
                    if not text or conn.execute('SELECT 1 FROM notification_messages WHERE item_id=?', (identity + ':0',)).fetchone():
                        continue
                    entry = dict(workspace=leader['project'], leader_session_id=sid, decision=None)
                    for number, part in enumerate(leader_parts(leader['project'], leader['crew_name'], text)):
                        key = '%s:%s' % (identity, number)
                        self.record_message(conn, key, entry, 'turn', self.service.now().isoformat())
                        self.deliver(conn, [key], self.client.send_markdown, part)

    def flush(self):
        notes = []
        now = self.service.now().isoformat()
        with closing(store.connect(self.home)) as conn:
            for item in self.pending.values():
                leader = self._leader(conn, item['session_id'])
                label = leader['crew_name'] or '未命名会话'
                decision = next((d for d in board.decisions(conn, item['project'], all=True)
                                 if d['id'] == item.get('decision_id') and d['status'] == 'open' and not d['held']), None)
                if item['kind'] == 'decision' and decision is None:
                    continue
                entry = dict(item=item, decision=decision, workspace=item['project'], leader=label,
                             leader_session_id=leader['session_id'])
                if decision:
                    self.record_message(conn, item['id'], entry, item['kind'], now)
                    self.deliver(conn, [item['id']], self.client.send_markdown, *self.question_message(entry))
                else:
                    notes.append(entry)
            if notes:
                text = '\n\n'.join('%s·%s\n%s\n%s' % (
                    e['workspace'], e['leader'], e['item']['title'], e['item']['detail']) for e in notes)
                for entry in notes:
                    self.record_message(conn, entry['item']['id'], entry, entry['item']['kind'], now)
                self.deliver(conn, [e['item']['id'] for e in notes], self.client.send_text, text)

    def record_message(self, conn, identity, entry, kind, now):
        conn.execute('INSERT INTO notification_messages(item_id,project,leader_session_id,decision_id,kind,created_at,content,status) VALUES(?,?,?,?,?,?,?,?)',
                     (identity, entry['workspace'], entry['leader_session_id'],
                      entry['decision']['id'] if entry['decision'] else None, kind, now,
                      json.dumps(entry, ensure_ascii=False), 'sending'))

    def deliver(self, conn, identities, method, *args):
        try:
            result = self.send(method, *args)
        except qq.QQError as exc:
            for identity in identities:
                conn.execute("UPDATE notification_messages SET status='error',error=? WHERE item_id=?",
                             ('%s(%s): %s' % (type(exc).__name__, exc.kind, exc), identity))
            raise
        for identity in identities:
            conn.execute("UPDATE notification_messages SET message_id=?,ref_idx=?,status='sent' WHERE item_id=?",
                         (result['id'], result['ref_idx'], identity))

    def question_message(self, entry):
        decision = entry['decision']
        text = '%s·%s 待拍板\n\n%s' % (
            entry['workspace'], entry['leader'], decision['question'])
        if decision['recommendation'] is not None:
            text += '\n\n建议：' + decision['recommendation']
        options = decision['options']
        if not options or len(options) > 25:
            if options:
                text += '\n\n' + '\n'.join('%s. %s' % (i + 1, option) for i, option in enumerate(options))
            return cjk_bold(text) + '\n\n请引用这条拍板消息回复。', None
        ordered = sorted(enumerate(options), key=lambda pair: pair[1] != decision['recommendation'])
        buttons = [dict(id=str(index),
                        render_data=dict(label=option, visited_label='已选：' + option,
                                         style=int(option == decision['recommendation'])),
                        action=dict(type=1, permission=dict(type=2), data='%s:%s' % (decision['id'], index),
                                    group_id=str(decision['id']), click_limit=1,
                                    unsupport_tips='请引用这条拍板消息回复')) for index, option in ordered]
        keyboard = dict(content=dict(rows=[dict(buttons=buttons[i:i + 5]) for i in range(0, len(buttons), 5)]))
        return cjk_bold(text) + '\n\n点按钮或引用拍板消息回复。', keyboard

    def receive(self, kind, event):
        try:
            if kind == 'interaction':
                with self.lock:
                    owner = self.config['owner_openid']
                if event['openid'] != owner:
                    LOG.info('Ignored QQ interaction from non-owner %s', event['openid'])
                    return
                # Leader startup and outbound sends can hold write_lock for seconds.
                self.client.ack_interaction(event['interaction_id'])
            with self.service.write_lock, closing(store.connect(self.home)) as conn:
                owner = self.config['owner_openid']
                if owner is None and kind == 'message':
                    with self.lock:
                        self.config['owner_openid'] = event['openid']
                        self.config['since'] = self.service.now().isoformat()
                        self.save_config()
                    owner = event['openid']
                    bound = True
                else:
                    bound = False
                if event['openid'] != owner:
                    LOG.info('Ignored QQ %s from non-owner %s', kind, event['openid'])
                    return
                if kind == 'interaction':
                    identity = event['interaction_id']
                else:
                    identity = event['message_id']
                if conn.execute('SELECT 1 FROM notification_inbound WHERE event_id=?', (identity,)).fetchone():
                    return
                conn.execute('INSERT INTO notification_inbound VALUES(?,?)', (identity, self.service.now().isoformat()))
                if bound:
                    self.send_text('已绑定为主人。发 /h 看命令；先用 /leader 和 /1 选择 leader。')
                elif kind == 'interaction':
                    self.answer_button(conn, event['data'], identity)
                else:
                    try:
                        self.receive_message(conn, event)
                    except (board.BoardError, OSError) as exc:
                        reason = '%s: %s' % (type(exc).__name__, exc)
                        self.error = reason
                        LOG.exception('QQ message processing failed')
                        self.send_text('消息未处理：' + reason)
        except qq.QQError as exc:
            self.fail_channel(exc)
            LOG.exception('QQ event processing failed')
        self.wake.set()

    def receive_message(self, conn, event):
        text, attachments = event['text'], event['attachments']
        if any(a['kind'] in ('video', 'file') for a in attachments):
            self.send_text('暂不支持')
            return
        voices = [a for a in attachments if a['kind'] == 'voice']
        if any(not a['voice_text'] for a in voices):
            self.send_text('没听清，请打字')
            return
        text = '\n'.join(part for part in [text, *(a['voice_text'] for a in voices)] if part)
        images = [a for a in attachments if a['kind'] == 'image']
        if text.strip().startswith('/'):
            self.handle_command(conn, dict(answer=text, message_id=event['message_id']))
            return
        if len(images) > 5:
            raise board.BoardError('一次最多发送 5 张图片')
        validated = []
        for image in images:
            if image['size'] is not None and image['size'] > chat.MAX_IMAGE_BYTES:
                raise board.BoardError('单张图片解码后不能超过 5 MB')
            try:
                raw = self.client.download(image['url'], chat.MAX_IMAGE_BYTES)
            except qq.QQError as exc:
                raise board.BoardError('图片下载失败：%s(%s): %s' % (type(exc).__name__, exc.kind, exc)) from exc
            validated.append(dict(media_type=image['content_type'], data=base64.b64encode(raw).decode('ascii')))
        validated = chat.validate_images(validated)
        shown = conn.execute("SELECT * FROM notification_messages WHERE ref_idx=? AND status='sent' AND kind IN ('decision','query') LIMIT 1",
                             (event['quoted_ref'],)).fetchone() if event['quoted_ref'] else None
        if shown is not None:
            if not text.strip():
                raise board.BoardError('请用文字答复这道拍板题')
            decision = next(d for d in board.decisions(conn, shown['project'], all=True) if d['id'] == shown['decision_id'])
            self.save_answer(conn, decision, text, event['message_id'])
        elif text.strip() or validated:
            self.queue_text(conn, dict(answer=text, images=validated, message_id=event['message_id']))
        else:
            self.send_text('请提供文字或图片')

    def answer_button(self, conn, data, identity):
        match = re.fullmatch(r'([1-9][0-9]*):([0-9]+)', data)
        if match is None:
            self.send_text('按钮无效，请先 /decisions。')
            return
        decision_id, index = int(match[1]), int(match[2])
        shown = conn.execute("SELECT project,content FROM notification_messages WHERE decision_id=? AND status='sent' AND kind IN ('decision','query') LIMIT 1",
                             (decision_id,)).fetchone()
        if shown is None:
            self.send_text('这道题尚未展示，请先 /decisions 查看待拍板。')
            return
        decision = next(d for d in board.decisions(conn, shown['project'], all=True) if d['id'] == decision_id)
        if index >= len(decision['options']):
            self.send_text('按钮选项序号无效，请先 /decisions。')
            return
        self.save_answer(conn, decision, decision['options'][index], identity)

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
        return [leader for project in dict.fromkeys(s['project'] for s in leaders)
                for leader in board.sort_leaders(conn, project, [s for s in leaders if s['project'] == project])]

    def handle_command(self, conn, reply):
        command, identity = reply['answer'].strip().lower(), reply['message_id']
        if command in ('/help', '/h'):
            text = HELP
        elif command in ('/back', '/b'):
            self.set_away(False)
            self.refresh_presence()
            if self.presence_error is not None:
                text = '已取消手动离开；当前无法确认是否仍因锁屏被视为离开。' + self.presence_error
            else:
                text = '已取消手动离开；当前仍因锁屏被视为离开。' if self.locked else '已取消手动离开；当前未因锁屏被视为离开。'
        elif command in ('/decisions', '/d'):
            return self.show_questions(conn, identity)
        elif command in ('/leader', '/l'):
            leaders = self.leader_catalog(conn)
            priorities = {}
            for project in dict.fromkeys(s['project'] for s in leaders):
                priorities.update(board.leader_priorities(conn, project, [s for s in leaders if s['project'] == project]))
            leaders.sort(key=lambda leader: priorities.get(leader['session_id'], 4))
            states = {}
            with self.service.agents_lock:
                agents, error = self.service.agents, self.service.agents_error
            for project in {s['project'] for s in leaders}:
                identities = [{'sessionId': s['session_id']} for s in board.sessions(conn, project)] if error else agents
                state = board.status(conn, project, identities)
                states.update((s['session_id'], s['status']) for s in [*state['ungrouped'], *(s for g in state['groups'] for s in g['members'])])
            pending = {}
            for decision in conn.execute("SELECT d.session_id FROM decisions d "
                                         "LEFT JOIN decision_holds h ON h.decision_id=d.id "
                                         "WHERE d.status='open' AND h.decision_id IS NULL"):
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
                    leader['number'], leader['project'],
                    ('★%s ' % priorities[sid] if sid in priorities else '') + leader['crew_name'],
                    current, status, unread, pending.get(sid, 0)))
            text = '\n'.join(lines) if lines else '目前没有有效的 leader。'
        else:
            numbered = re.fullmatch(r'/(?:leader\s+|l\s+)?([1-9][0-9]*)', command)
            if numbered:
                selected = next((s for s in self.leader_catalog(conn) if s['number'] == int(numbered[1])), None)
                if selected is None:
                    text = '这个 leader 编号已失效，请先 /leader。'
                else:
                    with self.lock:
                        self.config['current_leader_session_id'] = selected['session_id']
                        self.save_config()
                    text = '已切换到 %s·%s。' % (selected['project'], selected['crew_name'])
            else:
                text = '未知命令，请用 /h 查看帮助。'
        self.send_text(text)

    def show_questions(self, conn, identity):
        entries = []
        held_count = 0
        for project in board.projects(conn):
            for decision in board.decisions(conn, project['name']):
                if decision['held']:
                    held_count += 1
                    continue
                asker = board.session(conn, decision['session_id'])
                leader = asker if asker['role'] == 'leader' else board.leader_of(conn, asker['session_id'])
                label = leader['crew_name'] if leader is not None else '无leader'
                entries.append(dict(decision=decision, workspace=project['name'], leader=label,
                                    leader_session_id=leader['session_id'] if leader is not None else asker['session_id']))
        for entry in entries[:10]:
            key = 'query:%s:%s' % (identity, entry['decision']['id'])
            self.record_message(conn, key, entry, 'query', self.service.now().isoformat())
            self.deliver(conn, [key], self.client.send_markdown, *self.question_message(entry))
        if not entries:
            self.send_text('当前没有未挂起的待拍板。')
        elif len(entries) > 10:
            self.send_text('本次只显示前 10 道，剩余 %s 道未答拍板。' % (len(entries) - 10))
        if held_count:
            self.send_text('另有 %s 道挂起。' % held_count)

    def save_answer(self, conn, decision, answer, identity):
        if decision['status'] != 'open':
            self.send_text('这道题已处理，请先 /decisions 查看待拍板。')
            return
        asker = board.session(conn, decision['session_id'])
        if asker['status'] == 'closed' or board.is_archived(conn, asker['session_id']):
            self.send_text('这条会话已归档或移出 Crew，答复尚未保存。请先在 Crew 恢复会话。')
            return
        now = self.service.now().isoformat()
        board.answer(conn, decision['id'], answer, actor='qq', now=now)
        leader = asker if asker['role'] == 'leader' else board.leader_of(conn, asker['session_id'])
        if leader is not None:
            notice = '#%s\n提问者：%s（cc_name：%s）\n问题：%s\n答复：%s\n请马上用 SendMessage 转告并叫它继续' % (
                decision['id'], asker['crew_name'], asker['cc_name'], decision['question'], answer)
            board.queue_leader_notice(conn, leader['session_id'], notice, 'qq', now=now)
        preview = decision['question'][:20] + ('…' if len(decision['question']) > 20 else '')
        self.send_text('已保存：%s%s。' % (preview, '，已转告 leader' if leader else ''))

    def queue_text(self, conn, reply):
        session_id = self.config['current_leader_session_id']
        leaders = self.leader_catalog(conn)
        if session_id not in {s['session_id'] for s in leaders}:
            self.send_text('先用 /leader 和 /1 选一个 leader。')
            return
        conn.execute('INSERT INTO notification_replies VALUES(?,?,?,?,?,?)',
                     (reply['message_id'], session_id,
                      json.dumps(dict(text=reply['answer'], images=reply['images']), ensure_ascii=False),
                      'queued', None, self.service.now().isoformat()))
        leader = next(s for s in leaders if s['session_id'] == session_id)
        label = '%s·%s' % (leader['project'], leader['crew_name'])
        queued = conn.execute("SELECT 1 FROM notification_replies WHERE session_id=? AND status IN ('queued','running') AND message_id!=?",
                              (session_id, reply['message_id'])).fetchone()
        self.send_text(label + ' 正忙，排在后面' if self.service.chat.busy(session_id) or queued else '已转给 ' + label)

    def process_replies(self):
        with self.service.write_lock, closing(store.connect(self.home)) as conn:
            for row in conn.execute("SELECT * FROM notification_replies WHERE status IN ('queued','running') ORDER BY created_at,rowid").fetchall():
                session = board.session(conn, row['session_id'])
                sid = session['session_id']
                journal = self.service.chat.journal(sid)
                if row['status'] == 'queued':
                    if session['role'] != 'leader':
                        self._reply(conn, row, '这个会话已不是 leader，消息未发送。', 'error')
                        continue
                    if session['status'] == 'closed' or board.is_archived(conn, sid):
                        self._reply(conn, row, '这条会话已归档，消息未发送。', 'error')
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
                        self._reply(conn, row, '这个 leader 不在网页后台运行，收不到 QQ 消息', 'error')
                        continue
                    payload = json.loads(row['text'])
                    identity = 'qq-' + uuid.uuid5(uuid.NAMESPACE_URL, row['message_id']).hex
                    try:
                        self.service.chat.send(session, payload['text'], images=payload['images'],
                                               source='qq', identity=identity)
                    except (OSError, board.BoardError) as exc:
                        self._reply(conn, row, '消息未发送：%s: %s' % (type(exc).__name__, exc), 'error')
                        continue
                    # Resuming may drain the previous process before it writes this message.
                    after = next(e['seq'] for e in journal.events
                                 if e['type'] in ('message', 'compact_request') and e['id'] == identity)
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
                        self._reply(conn, row, text or '会话出错：' + error, 'answered')
                    elif end is not None and events[-1]['status'] == 'idle':
                        self._reply(conn, row, '（这一轮没有文字回复）', 'answered')
                    elif not self.service.chat.running(sid):
                        self._reply(conn, row, '会话已停止，还没有收到这条消息的回复。请在 Crew 查看会话。', 'error')

    def _reply(self, conn, row, text, status):
        identity = 'reply:' + row['message_id']
        # A persisted sending state surfaces uncertain delivery instead of sending twice.
        conn.execute("UPDATE notification_replies SET status='sending' WHERE message_id=?", (row['message_id'],))
        session = board.session(conn, row['session_id'])
        for index, part in enumerate(leader_parts(session['project'], session['crew_name'], text)):
            key = identity + ':' + str(index)
            entry = dict(workspace=session['project'], leader_session_id=session['session_id'], decision=None)
            self.record_message(conn, key, entry, 'reply', self.service.now().isoformat())
            self.deliver(conn, [key], self.client.send_markdown, part)
        conn.execute('UPDATE notification_replies SET status=? WHERE message_id=?', (status, row['message_id']))
