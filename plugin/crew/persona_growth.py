"""One cancellable personality growth job, shared by manual and scheduled triggers."""
import re
import sqlite3
import tempfile
import threading
from contextlib import closing
from datetime import timedelta

from . import behavior_prompts, board, channel_digest, persona, presentation, settings, store
from .chat_journal import Journal
from .ops.errors import OpError

DIALOGUE_LIMIT = 24000
WAKE_PREFIX = '[Crew] '
COMPACT_TEXT = '/compact'
NO_MATERIAL = '没有找到已接收的新私聊'
SUPERSEDED = '被用户修改覆盖，已放弃'
CANCELLED = 'Crew 已停止，本次性格整理已取消'
_registry = threading.Lock()
_active = None


def eligible_message(event, accepted, compact_ids):
    return (event['type'] == 'message' and event.get('role') == 'user'
            and event['id'] in accepted and event['id'] not in compact_ids
            and persona.direct_input(event.get('source'), event.get('room_id'), event['id'])
            and bool(event['text'].strip()) and not event['text'].startswith(WAKE_PREFIX)
            and event['text'].strip() != COMPACT_TEXT)


def sample(events, cursor, enabled_at):
    """Use the journal sequence as a cursor, including when timestamps coincide."""
    after = int(cursor) if cursor is not None else 0
    since = store.parse_iso(enabled_at)
    accepted = {e['message_id'] for e in events if e['type'] == 'input_delivery'
                and e['status'] == 'accepted' and e.get('room_id') is None}
    compact = {e['id'] for e in events if e['type'] == 'compact_request'}
    rooms = {e['message_id'] for e in events if e['type'] == 'input_delivery' and e.get('room_id') is not None}
    accepted.difference_update(rooms)
    turns, seen = [], set()
    current = None
    for e in events:
        kind = e['type']
        if kind == 'message':
            if e['id'] in seen:
                continue
            seen.add(e['id'])
            current = None
            if (e['seq'] > after and eligible_message(e, accepted, compact)
                    and e.get('at') and store.parse_iso(e['at']) > since):
                replies = []
                turns.append((e['seq'], e['text'], replies))
                current = replies
        elif kind in ('compact_request', 'turn_result') or e.get('room_id') is not None:
            if current is not None and kind == 'turn_result' and e['status'] != 'completed':
                current.clear()
            current = None
        elif current is not None and kind in ('text', 'assistant'):
            current.append(e)

    parts, remaining = [], DIALOGUE_LIMIT
    for _, user, replies in reversed(turns):
        # The open turn's trailing deltas may still change; only include completed snapshots there.
        if replies is current:
            while replies and replies[-1]['type'] == 'text':
                replies.pop()
        projected = presentation.chat_replies(replies)
        final = projected[-1]['text'] if projected else ''
        pair = '用户：' + user + ('\nAI：' + final if final else '')
        if remaining <= len('用户：'):
            break
        parts.append(pair[:remaining])
        remaining -= len(parts[-1]) + 2
    return '\n\n'.join(reversed(parts)), str(turns[-1][0]) if turns else cursor


def _sample(manager, sid, state):
    # A live journal shares its writer's condition. Read uncached files outside chat.lock.
    with manager.lock:
        journal = manager.journals.get(sid)
    if journal is None:
        journal = Journal(manager.path(sid), None)
    return sample(journal.after(0), state['growth_cursor'], state['enabled_at'])


def parse(text):
    match = re.fullmatch(r'变化：([^\r\n]+)\r?\n\s*正文：[ \t]*(.*?)', text.strip(), re.DOTALL)
    if match is None:
        raise ValueError('整理结果缺少一行变化说明或完整正文')
    summary, content = (part.strip() for part in match.groups())
    if (not summary or len(summary) > 60 or not content
            or re.search(r'^(变化|正文)：', content, re.MULTILINE)):
        raise ValueError('请用一句不超过 60 字的变化说明和一份完整正文')
    estimated = persona.estimate(content)
    if estimated > persona.LIMIT:
        raise ValueError('性格正文估算为 %d，超过上限 %d' % (estimated, persona.LIMIT))
    return summary, content


def _state(conn, sid):
    persona._require_subject(conn, sid)
    row = conn.execute('SELECT s.enabled,s.updated_at AS enabled_at,p.* FROM persona_settings s '
                       'LEFT JOIN personas p ON p.session_id=? WHERE s.singleton=1', (sid,)).fetchone()
    if row is None or not row['enabled']:
        raise OpError('persona_disabled', '性格已暂停')
    if row['frozen']:
        raise OpError('persona_frozen', '性格已定型')
    state = persona._EMPTY if row['session_id'] is None else dict(row)
    return {**state, 'session_id': sid, 'enabled_at': row['enabled_at'], 'growth_cursor': row['growth_cursor']}


def is_growing(session_id):
    with _registry:
        return _active is not None and _active.sid == session_id


class Job(threading.Thread):
    def __init__(self, manager, state, project):
        super().__init__(name='crew-persona-growth', daemon=True)
        self.manager, self.state, self.project = manager, state, project
        self.sid = state['session_id']
        self.cancelled = threading.Event()
        self.launched = threading.Event()
        self.lock = threading.Lock()
        self.process = None
        self.runtime = self.model = None

    def launch(self):
        global _active
        try:
            self.start()
        except RuntimeError:
            with _registry:
                _active = None
            raise
        finally:
            self.launched.set()
            self.manager.changes.mark('persona', self.project, self.sid)

    def spawn(self, command, **options):
        with self.lock:
            channel_digest._check_cancelled(self.cancelled)
            self.process = self.manager.process_factory(command, **options)
            return self.process

    def stop(self):
        with self.lock:
            self.cancelled.set()
            if self.process is not None and self.process.poll() is None:
                self.process.kill()

    def applicable(self, conn):
        session = conn.execute('SELECT s.* FROM sessions s JOIN persona_settings cfg ON cfg.singleton=1 '
                               'LEFT JOIN personas p USING(session_id) WHERE s.session_id=? AND s.project=? '
                               'AND cfg.enabled=1 AND cfg.updated_at=? AND COALESCE(p.frozen,0)=0',
                               (self.sid, self.project, self.state['enabled_at'])).fetchone()
        return (session is not None and persona._active(conn, session)
                and persona._visible_project(conn, self.project) and persona._subject(conn, session) is not None)

    def generate(self, conn):
        state = self.state
        dialogue, cursor = _sample(self.manager, self.sid, state)
        # A switch cycle during journal I/O invalidates this material before it reaches a carrier.
        if not self.applicable(conn) or not dialogue:
            return None
        error = state['growth_error']
        prompt = behavior_prompts.text(conn, 'persona_growth', session_id=self.sid, values={
            'current': state['content'], 'percent': persona._percent(state['tokens_estimate']), 'limit': '20k',
            'dialogue': dialogue,
            'last_error': '上次整理失败：%s，这次务必遵守篇幅和格式' % error if error and error != NO_MATERIAL else ''})
        choice = settings.resolve(self.manager.home, 'digest')
        self.runtime = choice['runtime']
        if self.runtime == 'claude':
            with tempfile.TemporaryDirectory(prefix='crew-persona-') as cwd:
                reply, _, self.model = channel_digest._claude(cwd, prompt, self.spawn, choice, receipt={})
        else:
            reply, _, self.model = channel_digest._codex(prompt, self.spawn, choice, self.cancelled, receipt={})
        summary, content = parse(reply)
        return summary, content, cursor

    def finish(self, conn, result, error):
        with self.lock, board.transaction(conn):
            if self.cancelled.is_set():
                error = CANCELLED
            sid = self.sid
            if not self.applicable(conn):
                return
            row = persona._current(conn, sid)
            now = store.now_iso()
            pending = max(0, row['pending_turns'] - self.state['pending_turns'])
            if error is not None and row['version'] != self.state['version']:
                error = SUPERSEDED
            if error is None and result is None:
                conn.execute('UPDATE personas SET growth_error=NULL WHERE session_id=?', (sid,))
            elif error is None:
                summary, content, cursor = result
                status = persona.write_growth(conn, sid, content, summary, self.state['version'], cursor, now)
                if status in ('disabled', 'frozen', 'not_applicable'):
                    return
                if status == 'superseded':
                    error = SUPERSEDED
            if error is not None:
                persona.record_growth_error(conn, sid, error)
            # Later arrivals still count. An unsuccessful job needs a new trigger.
            conn.execute('UPDATE personas SET pending_turns=? WHERE session_id=?', (pending, sid))
            if self.runtime:
                board._event(conn, 'persona:growth', self.project, sid,
                             {'runtime': self.runtime, 'model': self.model, 'written': error is None}, now)

    def run(self):
        global _active
        try:
            with closing(store.connect(self.manager.home)) as conn:
                if self.cancelled.is_set():
                    self.finish(conn, None, CANCELLED)
                    return
                try:
                    result = self.generate(conn)
                    self.finish(conn, result, None)
                except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
                    self.finish(conn, None, str(exc))
        finally:
            with _registry:
                _active = None
                self.manager.changes.mark('persona', self.project, self.sid)


def _reserve(manager, state, project):
    """Called with the registry locked. Native work starts after releasing chat.lock."""
    global _active
    _active = Job(manager, state, project)
    return _active


def grow(manager, conn, session_id):
    checked = None
    dialogue = ''
    while True:
        with manager.lock, _registry:
            state = _state(conn, session_id)
            if _active is not None or manager.closed:
                raise OpError('persona_busy', '现在不能开始整理性格')
            boundary = (state['enabled_at'], state['growth_cursor'])
            if state['pending_turns'] or checked == boundary:
                if not state['pending_turns'] and not dialogue:
                    raise OpError('persona_nothing_new', NO_MATERIAL)
                job = _reserve(manager, state, board.session(conn, session_id)['project'])
                break
        dialogue, _ = _sample(manager, session_id, state)
        checked = boundary
    job.launch()
    return {'started': True}


def tick(manager, conn, now):
    cutoff = (store.parse_iso(now) - timedelta(hours=6)).isoformat()
    with manager.lock, _registry:
        if manager.closed or _active is not None or not persona.enabled(conn):
            return
        due = conn.execute(
            "SELECT p.*,s.project,cfg.updated_at AS enabled_at FROM personas p JOIN sessions s USING(session_id) "
            "JOIN persona_settings cfg ON cfg.singleton=1 AND cfg.enabled=1 "
            "JOIN projects w ON w.name=s.project LEFT JOIN project_settings ps ON ps.project=s.project "
            "LEFT JOIN session_controls c ON c.session_id=s.session_id "
            "WHERE p.frozen=0 AND ps.removed_at IS NULL AND s.status!='closed' "
            "AND COALESCE(c.removed,0)=0 AND c.archived_status IS NULL AND "
            "((p.version=0 AND p.content='' AND p.pending_turns>=4) OR "
            "(p.pending_turns>=12 AND (p.last_growth_at IS NULL OR julianday(p.last_growth_at)<=julianday(?)))) "
            "ORDER BY p.pending_turns DESC,p.session_id", (cutoff,))
        for row in due:
            if persona.subject(conn, row['session_id']):
                job = _reserve(manager, dict(row), row['project'])
                break
        else:
            return
    job.launch()


def stop(manager):
    with _registry:
        job = _active
    if job is not None and job.manager is manager:
        job.stop()


def close(manager):
    with _registry:
        job = _active
    if job is not None and job.manager is manager:
        job.stop()
        job.launched.wait()
        if job.ident is not None:
            job.join()
