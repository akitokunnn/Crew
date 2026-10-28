"""Program notifications with durable, independent channel delivery receipts.

An unconfirmed send is retained as unknown; it is never blindly replayed after
a restart. Only explicit platform rejection can enter the bounded retry path.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlencode, urlsplit

from . import board, goals, qq, store


MAX_RETRIES = 2
CHANNELS = ('qq', 'lark')


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS task_notification_events (
        id TEXT PRIMARY KEY, goal_id TEXT NOT NULL REFERENCES goals(id), kind TEXT NOT NULL,
        text TEXT NOT NULL, source_json TEXT NOT NULL, created_at TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS task_notification_receipts (
        event_id TEXT NOT NULL REFERENCES task_notification_events(id), channel TEXT NOT NULL,
        status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, recipient_hash TEXT,
        message_id TEXT, error TEXT, next_attempt REAL, updated_at TEXT NOT NULL,
        PRIMARY KEY(event_id,channel))''')


def _exists(conn, table):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _active(conn, goal_id):
    if not _exists(conn, 'task_controls'):
        return True
    control = conn.execute('SELECT lifecycle FROM task_controls WHERE goal_id=?', (goal_id,)).fetchone()
    return control is None or control[0] == 'active'


class TaskNotifications:
    def __init__(self, home, connections, *, clock=None, base_url=None):
        self.home, self.connections = Path(home), connections
        self.clock = clock or time.time
        self.base_url = '' if base_url is None else base_url.rstrip('/')
        if self.base_url:
            parsed = urlsplit(self.base_url)
            if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                    or parsed.path or parsed.query or parsed.fragment):
                raise board.BoardError('通知入口必须是无凭证的 HTTPS origin')
        self.tick_lock = threading.Lock()
        with closing(store.connect(self.home)) as conn, goals.transaction(conn):
            ensure_schema(conn)
            conn.execute("UPDATE task_notification_receipts SET status='unknown',next_attempt=NULL,error=? "
                         "WHERE status='sending'", ('服务恢复时发送结果尚未确认，未自动重发',))

    def _entry(self, goal):
        return self.base_url + '/?' + urlencode({'project': goal['project']}) + '#' + urlencode(
            {'leader': goal['leader_session_id'], 'goal': goal['id']})

    def _event(self, conn, identity, goal_id, kind, summary, source, at):
        goal = goals.get(conn, goal_id)
        body = '%s · %s\n%s\n入口：%s' % (goal['project'], goal['title'], summary, self._entry(goal))
        conn.execute('INSERT OR IGNORE INTO task_notification_events VALUES(?,?,?,?,?,?)',
                     (identity, goal_id, kind, body[:1900], json.dumps(source, ensure_ascii=False), at))

    def _discover(self, conn):
        if _exists(conn, 'task_failure_notifications'):
            for row in conn.execute('SELECT * FROM task_failure_notifications ORDER BY created_at,id'):
                self._event(conn, row['id'], row['goal_id'], 'failure',
                    '自动恢复已达上限，需要处理：' + row['error'][:700], {'work_id': row['work_id']}, row['created_at'])
        if _exists(conn, 'task_artifact_versions'):
            for row in conn.execute('SELECT * FROM task_artifact_versions ORDER BY created_at,id'):
                # Imported snapshots are history, not newly delivered work.
                if 'import_absent_paths' in json.loads(row['source_json']):
                    continue
                self._event(conn, 'task-stage:' + row['id'], row['goal_id'], 'stage',
                    '阶段成果 v%s：%s' % (row['version'], row['label']), {'version_id': row['id']}, row['created_at'])
        links = {}
        if _exists(conn, 'personal_todo_tasks'):
            for row in conn.execute('SELECT todo_id,goal_id FROM personal_todo_tasks ORDER BY goal_id'):
                links.setdefault(row['todo_id'], []).append(row['goal_id'])
        if _exists(conn, 'goal_links'):
            for row in conn.execute("SELECT item_id,goal_id FROM goal_links WHERE kind='todo' ORDER BY goal_id"):
                links.setdefault(row['item_id'], []).append(row['goal_id'])
        for todo_id, goal_ids in links.items():
            item = conn.execute("SELECT * FROM todos WHERE id=? AND status='open'", (todo_id,)).fetchone()
            if item is None:
                continue
            active = sorted(goal_id for goal_id in set(goal_ids) if _active(conn, goal_id))
            self._event(conn, 'task-todo:' + str(todo_id), (active or sorted(set(goal_ids)))[0], 'todo',
                '需要你操作 #%s：%s\n%s' % (todo_id, item['title'], item['steps'][:700]),
                {'todo_id': todo_id, 'goal_ids': sorted(set(goal_ids))}, item['created_at'])

    def _receipt(self, conn, event, channel, inbox):
        row = conn.execute('SELECT * FROM task_notification_receipts WHERE event_id=? AND channel=?',
                           (event['id'], channel)).fetchone()
        if row is not None:
            return dict(row)
        status, message, error = 'queued', None, None
        if event['kind'] == 'todo' and inbox is not None:
            original = 'todo:' + str(json.loads(event['source_json'])['todo_id'])
            identity = inbox.item_key(original)
            # Imports archive the original identity before this machine has a
            # channel binding. That history marker applies to every generation.
            prior = inbox.execute(conn, 'SELECT * FROM notification_messages WHERE item_id=? '
                "OR (item_id=? AND status='archived') ORDER BY CASE WHEN status='archived' THEN 0 ELSE 1 END LIMIT 1",
                (identity, original)).fetchone()
            if prior:
                status = 'sent' if prior['status'] == 'sent' else 'archived' if prior['status'] == 'archived' else 'unknown'
                message, error = prior['message_id'], prior['error']
            else:
                goal = goals.get(conn, event['goal_id'])
                inbox.execute(conn, 'INSERT OR IGNORE INTO notification_messages('
                    'item_id,project,leader_session_id,kind,created_at,content,status) VALUES(?,?,?,?,?,?,?)',
                    (identity, goal['project'], goal['leader_session_id'], 'todo', event['created_at'],
                     json.dumps({'task_notification_event': event['id']}), 'managed'))
        conn.execute('INSERT OR IGNORE INTO task_notification_receipts VALUES(?,?,?,?,?,?,?,?,?)',
                     (event['id'], channel, status, 0, None, message, error, None, store.now_iso()))
        return dict(conn.execute('SELECT * FROM task_notification_receipts WHERE event_id=? AND channel=?',
                                 (event['id'], channel)).fetchone())

    def _set(self, conn, event, channel, status, *, error=None, message_id=None, retry_at=None):
        conn.execute('UPDATE task_notification_receipts SET status=?,error=?,message_id=?,next_attempt=?,updated_at=? '
                     'WHERE event_id=? AND channel=?',
                     (status, error, message_id, retry_at, store.now_iso(), event['id'], channel))

    def _deliver(self, conn, event, channel):
        inbox = self.connections.inboxes.get(channel)
        with goals.transaction(conn):
            receipt = self._receipt(conn, event, channel, inbox)
            if receipt['status'] not in ('queued', 'retry'):
                return
            related = json.loads(event['source_json']).get('goal_ids', [event['goal_id']])
            if not any(_active(conn, goal_id) for goal_id in related):
                self._set(conn, event, channel, 'archived', error='关联任务已归档或移入垃圾箱，历史通知不再发送')
                return
            if receipt['next_attempt'] is not None and receipt['next_attempt'] > self.clock():
                return
            if not self.connections.allows_push(channel):
                self._set(conn, event, channel, 'disabled', error='用户未选择此主动通知渠道')
                return
            if (inbox is None or not inbox.enabled or inbox.credentials is None
                    or inbox.config.get('owner_openid') is None or inbox.client is None):
                self._set(conn, event, channel, 'not_configured', error='渠道未配置、未连接或尚未绑定接收人')
                return
            if event['kind'] == 'todo':
                todo_id = json.loads(event['source_json'])['todo_id']
                current = conn.execute('SELECT status FROM todos WHERE id=?', (todo_id,)).fetchone()
                if current is None or current[0] != 'open':
                    self._set(conn, event, channel, 'archived', error='待办已处理，不再发送新事项通知')
                    return
            recipient = hashlib.sha256((channel + ':' + inbox.config['owner_openid'] + ':' +
                str(inbox.config.get('generation', ''))).encode()).hexdigest()
            if receipt['recipient_hash'] is not None and receipt['recipient_hash'] != recipient:
                self._set(conn, event, channel, 'owner_changed', error='接收人绑定已变化，未向新接收人重发旧通知')
                return
            conn.execute("UPDATE task_notification_receipts SET status='sending',attempts=attempts+1,"
                         'recipient_hash=?,updated_at=? WHERE event_id=? AND channel=?',
                         (recipient, store.now_iso(), event['id'], channel))
        # The sending intent is durable before the external effect.
        try:
            result = inbox.send_text(event['text'])
            if not isinstance(result, dict) or not isinstance(result.get('id'), str) or not result['id']:
                with goals.transaction(conn):
                    self._set(conn, event, channel, 'unknown', error='渠道返回时缺少可核实的消息 ID，未自动重发')
                return
        except Exception as error:
            detail = type(error).__name__ + ': ' + str(error)
            attempts = receipt['attempts'] + 1
            # Network loss can follow a successful external send. A platform
            # rejection with a response is distinguishable and safe to retry.
            known_rejection = isinstance(error, qq.QQError) and (
                error.kind == 'rate_limited' or error.status is not None and error.status >= 400
                or error.code is not None and error.kind != 'network')
            if known_rejection and attempts <= MAX_RETRIES:
                status, retry_at = 'retry', self.clock() + min(60, 5 * 2 ** (attempts - 1))
            else:
                status, retry_at = ('failed' if known_rejection else 'unknown'), None
            with goals.transaction(conn):
                self._set(conn, event, channel, status, error=detail[:2000], retry_at=retry_at)
            return
        with goals.transaction(conn):
            self._set(conn, event, channel, 'sent', message_id=result['id'])

    def tick(self):
        if not self.tick_lock.acquire(blocking=False):
            return
        try:
            with closing(store.connect(self.home)) as conn:
                ensure_schema(conn)
                with goals.transaction(conn):
                    self._discover(conn)
                pending = conn.execute('SELECT e.* FROM task_notification_events e WHERE EXISTS('
                    'SELECT 1 FROM (SELECT ? channel UNION ALL SELECT ?) c '
                    'LEFT JOIN task_notification_receipts r ON r.event_id=e.id AND r.channel=c.channel '
                    "WHERE r.status IS NULL OR r.status IN ('queued','retry')) ORDER BY e.created_at,e.id LIMIT 20",
                    CHANNELS).fetchall()
                for event in pending:
                    for channel in CHANNELS:
                        # A connection replacement/owner reset cannot race a
                        # send into a different recipient while this lock holds.
                        with self.connections.lock:
                            self._deliver(conn, dict(event), channel)
        finally:
            self.tick_lock.release()

    def snapshot(self, goal_id=None):
        with closing(store.connect(self.home)) as conn:
            ensure_schema(conn)
            args = () if goal_id is None else (goal_id,)
            records = conn.execute('SELECT e.id,e.goal_id,e.kind,e.text,e.created_at,r.channel,r.status,r.attempts,'
                'r.message_id,r.error,r.next_attempt,r.updated_at FROM task_notification_events e '
                'JOIN task_notification_receipts r ON r.event_id=e.id' +
                ('' if goal_id is None else ' WHERE e.goal_id=?') + ' ORDER BY e.created_at,e.id,r.channel', args)
            return {'max_retries': MAX_RETRIES, 'receipts': [dict(row) for row in records]}
