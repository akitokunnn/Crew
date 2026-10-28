"""Department compaction and current-workspace cleanup.

Department compaction resumes only existing native histories of a leader and
its current members and submits the fixed native compact command (Claude
``/compact``, Codex ``thread/compact/start``). Goal state, reports and
acceptance never change; busy, external or unconfirmable contexts are reported
with their real reason instead of being stopped. An item completes only on the
native compact event.

Cleanup stops Crew-owned natives, cancels unfinished goals and then, in one
transaction, archives the workspace's departments and identities and moves its
channel, digest and handoff boundaries. Original conversations, deliverables,
files and settings are kept, and the older channel and handoffs stay readable
from the cleanup history.
"""
import hashlib
import json
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import closing

from . import board, codex_client, codex_runtime, git_admin, goal_context, goals, runtimes, session_ops, store

COMPACT_TIMEOUT = 900   # seconds a native compaction may take before the item fails
CONFIRM_GRACE = 30      # seconds to wait for the compact event after the native request has ended
PREVIEW_TTL = 600       # seconds a preview stays valid for execution
POLL_SECONDS = 1.0
KINDS = ('department_compact', 'workspace_cleanup')
INTERRUPTED = '服务重启时尚未结束；没有自动重发，需要时请重新预览后执行'
LAUNCH_ERRORS = (board.BoardError, OSError, codex_client.CodexError, subprocess.SubprocessError, ValueError, sqlite3.Error)


class ItemFailure(Exception):
    """A classified item outcome recorded with its type and real reason."""

    def __init__(self, kind, reason, detail=None):
        super().__init__(reason)
        self.kind, self.reason, self.detail = kind, reason, detail or {}


def _describe(exc):
    return '%s: %s' % (type(exc).__name__, exc)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def recover(conn, now=None):
    """A restarted service never resumes maintenance; unfinished entries become interrupted."""
    at = store.now_iso() if now is None else now
    with board.transaction(conn):
        jobs = [row['id'] for row in conn.execute("SELECT id FROM maintenance_jobs WHERE status='running'")]
        for job_id in jobs:
            conn.execute("UPDATE maintenance_items SET status='interrupted',error_type='interrupted',error=?,updated_at=? "
                         "WHERE job_id=? AND status IN ('pending','running')", (INTERRUPTED, at, job_id))
            conn.execute("UPDATE maintenance_jobs SET status='interrupted',error_type='interrupted',error=?,updated_at=?,finished_at=? "
                         "WHERE id=?", (INTERRUPTED, at, at, job_id))
    return jobs


def _job(conn, row):
    value = dict(row)
    value['detail'] = json.loads(value.pop('detail_json'))
    value['items'] = []
    for item in conn.execute('SELECT * FROM maintenance_items WHERE job_id=? ORDER BY position', (value['id'],)):
        entry = dict(item)
        entry['detail'] = json.loads(entry.pop('detail_json'))
        value['items'].append(entry)
    value['counts'] = {status: sum(item['status'] == status for item in value['items'])
                       for status in ('pending', 'running', 'completed', 'failed', 'interrupted')}
    return value


def job(conn, project, job_id):
    board.project(conn, project)
    row = conn.execute('SELECT * FROM maintenance_jobs WHERE id=?', (job_id,)).fetchone()
    if row is None or row['project'] != project:
        raise board.BoardError('维护记录不存在或不属于当前工作区')
    return _job(conn, row)


def jobs(conn, project, kind, leader_session_id=None, limit=10):
    board.project(conn, project)
    if kind not in KINDS:
        raise board.BoardError('维护类型无效')
    if kind == 'department_compact':
        leader = board.session(conn, leader_session_id) if isinstance(leader_session_id, str) else None
        if leader is None or leader['project'] != project:
            raise board.BoardError('leader 不属于当前工作区')
        rows = conn.execute('SELECT * FROM maintenance_jobs WHERE project=? AND kind=? AND leader_session_id=? '
                            'ORDER BY created_at DESC,rowid DESC LIMIT ?', (project, kind, leader_session_id, limit))
    else:
        rows = conn.execute('SELECT * FROM maintenance_jobs WHERE project=? AND kind=? ORDER BY created_at DESC,rowid DESC LIMIT ?',
                            (project, kind, limit))
    return [_job(conn, row) for row in rows.fetchall()]


def archive(conn, project, job_id, kind, before=None, limit=50):
    """Read-only content that a completed cleanup moved behind the workspace boundary."""
    value = job(conn, project, job_id)
    if value['kind'] != 'workspace_cleanup' or 'boundary' not in value['detail']:
        raise board.BoardError('这次清理没有建立边界，没有可查看的旧内容')
    if before is not None and (type(before) is not int or before < 1):
        raise board.BoardError('before 必须是正整数')
    if type(limit) is not int or not 1 <= limit <= 200:
        raise board.BoardError('limit 必须在 1 到 200 之间')
    ranges = value['detail']['boundary']
    if kind == 'channel':
        span = ranges['channel']
        sql = 'SELECT * FROM channel_messages WHERE project=? AND id>? AND id<=?'
        args = [project, span['after'], span['upto']]
        if before is not None:
            sql += ' AND id<?'
            args.append(before)
        rows = conn.execute(sql + ' ORDER BY id DESC LIMIT ?', (*args, limit + 1)).fetchall()
        from . import channel
        digest = conn.execute('SELECT id,upto_message_id,body,created_at,model,runtime FROM channel_digests '
                              'WHERE project=? AND id>? AND id<=? ORDER BY id DESC LIMIT 1',
                              (project, ranges['digest']['after'], ranges['digest']['upto'])).fetchone()
        return {'job_id': job_id, 'kind': kind, 'messages': [channel._message(conn, row) for row in reversed(rows[:limit])],
                'has_more': len(rows) > limit, 'digest': dict(digest) if digest is not None else None}
    if kind == 'handoffs':
        span = ranges['handoff']
        sql = ('SELECT h.*,s.crew_name AS from_name FROM handoffs h LEFT JOIN sessions s ON s.session_id=h.from_session '
               'WHERE h.project=? AND h.id>? AND h.id<=?')
        args = [project, span['after'], span['upto']]
        if before is not None:
            sql += ' AND h.id<?'
            args.append(before)
        rows = conn.execute(sql + ' ORDER BY h.id DESC LIMIT ?', (*args, limit + 1)).fetchall()
        return {'job_id': job_id, 'kind': kind, 'handoffs': [dict(row) for row in reversed(rows[:limit])],
                'has_more': len(rows) > limit}
    raise board.BoardError('只能查看 channel 或 handoffs')


def _department(conn, project, leader_session_id):
    board.project(conn, project)
    if not isinstance(leader_session_id, str) or not leader_session_id:
        raise board.BoardError('请指定部门 leader')
    leader = board.session(conn, leader_session_id)
    group = conn.execute('SELECT * FROM groups WHERE project=? AND leader_session_id=? AND closed_at IS NULL',
                         (project, leader_session_id)).fetchone()
    if (leader['project'] != project or leader['role'] != 'leader' or group is None
            or leader['status'] == 'closed' or board.is_archived(conn, leader_session_id)):
        raise board.BoardError('leader 不属于当前工作区，或部门已经结束')
    members = [dict(row) for row in conn.execute(
        "SELECT * FROM sessions WHERE project=? AND group_name=? AND role='member' AND status<>'closed' "
        "ORDER BY crew_name,session_id", (project, group['name']))
        if not board.is_archived(conn, row['session_id'])]
    return leader, dict(group), members


class _Agents:
    """Fetch external Claude processes at most once, and only when a Claude native is involved."""

    def __init__(self, chat):
        self.chat, self.loaded, self.value, self.error = chat, False, None, None

    def get(self):
        if not self.loaded:
            self.loaded = True
            try:
                self.value = self.chat._external_agents()
            except board.Conflict as exc:
                self.error = str(exc)
        return self.value


class Manager:
    def __init__(self, service):
        self.service, self.chat, self.home = service, service.chat, service.home
        self.lock = threading.Lock()
        self.previews, self.workers = {}, []
        self.stopping = threading.Event()
        with closing(store.connect(self.home)) as conn:
            recover(conn)

    def close(self):
        self.stopping.set()
        for worker in list(self.workers):
            worker.join(timeout=20)

    # ------------------------------------------------------------------ previews

    def _remember(self, kind, project, value):
        identity, now = uuid.uuid4().hex, time.monotonic()
        with self.lock:
            for key in [key for key, entry in self.previews.items() if entry['deadline'] <= now]:
                del self.previews[key]
            self.previews[identity] = {**value, 'kind': kind, 'project': project, 'deadline': now + PREVIEW_TTL}
        return identity

    def _preview(self, kind, project, preview_id):
        with self.lock:
            entry = self.previews.get(preview_id) if isinstance(preview_id, str) else None
        if entry is None or entry['kind'] != kind or entry['project'] != project:
            raise board.Conflict('预览已失效，请重新预览')
        if entry['deadline'] <= time.monotonic():
            raise board.Conflict('预览已超过 %d 分钟，请重新预览' % (PREVIEW_TTL // 60))
        return entry

    def _existing(self, conn, project, kind, request_id):
        if not isinstance(request_id, str) or not request_id:
            return None
        row = conn.execute('SELECT * FROM maintenance_jobs WHERE project=? AND kind=? AND request_id=?',
                           (project, kind, request_id)).fetchone()
        return _job(conn, row) if row is not None else None

    # ------------------------------------------------------------------ persistence

    @staticmethod
    def _department_replay(value, leader_session_id, targets):
        if (value['leader_session_id'] != leader_session_id or
                {item['target'] for item in value['items']} != set(targets)):
            raise board.Conflict('这份预览已经用于其他选择，请重新预览')

    def _create(self, conn, project, kind, request_id, items, detail, leader_session_id=None):
        at = store.now_iso()
        with board.transaction(conn):
            row = conn.execute('SELECT * FROM maintenance_jobs WHERE project=? AND kind=? AND request_id=?',
                               (project, kind, request_id)).fetchone()
            if row is not None:
                if kind == 'department_compact':
                    self._department_replay(_job(conn, row), leader_session_id, [item['target'] for item in items])
                return row['id'], False
            busy = conn.execute("SELECT kind FROM maintenance_jobs WHERE project=? AND status='running' AND "
                                "(kind='workspace_cleanup' OR ?='workspace_cleanup' OR leader_session_id=?)",
                                (project, kind, leader_session_id)).fetchone()
            if busy is not None:
                raise board.Conflict('工作区清理正在进行，完成后再操作' if busy['kind'] == 'workspace_cleanup'
                                     else '部门压缩正在进行，完成后再操作')
            identity = uuid.uuid4().hex
            conn.execute('INSERT INTO maintenance_jobs(id,project,kind,leader_session_id,request_id,status,detail_json,'
                         'created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                         (identity, project, kind, leader_session_id, request_id, 'running', _json(detail), at, at))
            for position, item in enumerate(items):
                conn.execute('INSERT INTO maintenance_items(job_id,position,target,session_id,execution_id,label,status,'
                             'detail_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                             (identity, position, item['target'], item.get('session_id'), item.get('execution_id'),
                              item['label'], 'pending', _json(item.get('detail', {})), at))
            board._event(conn, 'maintenance:' + kind, project, board.HUMAN, {'job_id': identity, 'items': len(items)}, at)
            return identity, True

    def _item(self, job_id, position, status, kind=None, reason=None, detail=None):
        with closing(store.connect(self.home)) as conn, board.transaction(conn):
            conn.execute('UPDATE maintenance_items SET status=?,error_type=?,error=?,'
                         'detail_json=CASE WHEN ? IS NULL THEN detail_json ELSE ? END,updated_at=? WHERE job_id=? AND position=?',
                         (status, kind, reason, None if detail is None else 1, None if detail is None else _json(detail),
                          store.now_iso(), job_id, position))

    def _merge_detail(self, job_id, position, extra):
        with closing(store.connect(self.home)) as conn:
            row = conn.execute('SELECT detail_json FROM maintenance_items WHERE job_id=? AND position=?', (job_id, position)).fetchone()
        return {**json.loads(row['detail_json']), **extra}

    def _finish(self, job_id, kind=None, reason=None, detail=None):
        """Derive the job outcome from its items; a job-level error marks unfinished items failed."""
        at = store.now_iso()
        with closing(store.connect(self.home)) as conn, board.transaction(conn):
            if kind is not None:
                conn.execute("UPDATE maintenance_items SET status='failed',error_type=?,error=?,updated_at=? "
                             "WHERE job_id=? AND status IN ('pending','running')", (kind, reason, at, job_id))
            states = [row['status'] for row in conn.execute('SELECT status FROM maintenance_items WHERE job_id=?', (job_id,))]
            done = states.count('completed')
            status = ('completed' if states and done == len(states) and kind is None else
                      'partial' if done else 'failed')
            if detail is not None:
                current = json.loads(conn.execute('SELECT detail_json FROM maintenance_jobs WHERE id=?', (job_id,)).fetchone()[0])
                conn.execute('UPDATE maintenance_jobs SET detail_json=? WHERE id=?', (_json({**current, **detail}), job_id))
            conn.execute('UPDATE maintenance_jobs SET status=?,error_type=?,error=?,updated_at=?,finished_at=? WHERE id=?',
                         (status, kind, reason, at, at, job_id))

    def _start_worker(self, target, *args):
        worker = threading.Thread(target=target, args=args, name='crew-maintenance', daemon=True)
        with self.lock:
            self.workers = [item for item in self.workers if item.is_alive()] + [worker]
        worker.start()

    # ------------------------------------------------------------------ department compaction

    def _contexts(self, conn, project, person):
        sid, name = person['session_id'], person['crew_name'] or person['session_id']
        contexts = []
        mapping = runtimes.get(conn, sid)
        bound = conn.execute('SELECT 1 FROM session_runtime_bindings WHERE session_id=? AND native_id IS NOT NULL', (sid,)).fetchone()
        if mapping['native_id'] is not None or bound is not None or self.chat.path(sid).exists():
            contexts.append({'target': sid, 'session_id': sid, 'execution_id': None, 'person': name,
                             'role': person['role'], 'context': '总对话', 'runtime': mapping['runtime'],
                             'goal_id': None, 'goal_title': None, 'goal_status': None})
        chosen = {}
        for row in conn.execute('SELECT e.id,e.goal_id FROM goal_executions e JOIN goals g ON g.id=e.goal_id '
                                'WHERE e.session_id=? AND g.project=? ORDER BY g.created_at,g.id,e.round DESC,e.generation DESC',
                                (sid, project)):
            execution = goals.get_execution(conn, row['id'])
            bound = any(item['runtime'] == execution['runtime'] and item['native_id'] for item in execution['bindings'])
            previous = chosen.get(row['goal_id'])
            # Each goal's newest execution with a native history; superseded generations are never loaded again.
            if previous is None and (bound or self.chat.path(execution['id']).exists()):
                chosen[row['goal_id']] = (execution, bound)
            elif previous is not None and not previous[1] and bound:
                chosen[row['goal_id']] = (execution, bound)
        for goal_id, (execution, _) in chosen.items():
            goal = goals.get(conn, goal_id)
            contexts.append({'target': execution['id'], 'session_id': sid, 'execution_id': execution['id'], 'person': name,
                             'role': person['role'], 'context': '目标「%s」' % goal['title'][:60], 'runtime': execution['runtime'],
                             'goal_id': goal_id, 'goal_title': goal['title'], 'goal_status': goal['status']})
        return contexts

    def _label(self, conn, key, sid):
        if key == sid:
            return '总对话'
        return '目标「%s」' % goals.get(conn, goals.get_execution(conn, key)['goal_id'])['title'][:60]

    def _natives(self, conn, sid, keys):
        """Every native history of one Crew identity: Claude ids by owner key, and keys with a Codex history."""
        claude, codex = {}, []
        for key in keys:
            if key == sid:
                mapping = runtimes.get(conn, sid)
                bindings = [dict(row) for row in conn.execute(
                    'SELECT runtime,native_id FROM session_runtime_bindings WHERE session_id=? AND native_id IS NOT NULL', (sid,))]
                if mapping['runtime'] == 'claude':
                    claude[mapping['native_id'] or sid] = key
                used = {mapping['runtime']}
            else:
                execution = goals.get_execution(conn, key)
                bindings, used = execution['bindings'], {execution['runtime']}
            used.update(binding['runtime'] for binding in bindings)
            claude.update({binding['native_id']: key for binding in bindings if binding['runtime'] == 'claude' and binding['native_id']})
            if 'codex' in used:
                codex.append(key)
        return claude, codex

    def _check(self, conn, context, agents):
        """Whether one context can be compacted now; never starts or stops anything."""
        chat, key, sid = self.chat, context['target'], context['session_id']
        execution = goals.get_execution(conn, key) if context['execution_id'] else None
        current = chat.processes.get(key)
        keys = chat._identity_keys(conn, sid)
        for other in keys:
            if other != key and chat.running(other) and (chat.busy(other) or chat.processes[other].pending_compact):
                return {'kind': 'identity_in_use', 'running': chat.running(key),
                        'reason': '该人员的%s正在处理工作，空闲后可压缩' % self._label(conn, other, sid)}
        if chat.running(key):
            if getattr(current, 'maintenance', None) is not None:
                return {'kind': 'busy', 'reason': '已有维护压缩正在处理这段上下文', 'running': True}
            if chat.busy(key) or current.pending_compact:
                return {'kind': 'busy', 'reason': '正在工作，空闲后可压缩', 'running': True}
            return {'kind': None, 'reason': '空闲，可直接压缩', 'running': True}
        if current is not None and not current.reader_done.is_set():
            return {'kind': 'busy', 'reason': '上一段进程输出尚未排空', 'running': False}
        if execution is not None:
            if execution['status'] in goals.ACTIVE_EXECUTIONS:
                return {'kind': 'busy', 'reason': '目标执行仍标记为进行中（等待续接或外部运行），没有压缩', 'running': False}
            if not any(item['runtime'] == execution['runtime'] and item['native_id'] for item in execution['bindings']):
                return {'kind': 'no_history', 'reason': '这段目标执行没有原生会话，无需压缩', 'running': False}
        else:
            if runtimes.get(conn, sid)['native_id'] is None:
                return {'kind': 'no_history', 'reason': '当前执行工具尚未建立原生会话（执行工具切换尚未完成），无需压缩',
                        'running': False}
            if not chat.path(sid).exists():
                return {'kind': 'no_web_record', 'reason': '没有网页聊天记录；请在原工具中输入 /compact', 'running': False}
        person = board.session(conn, sid)
        if person['spawn_id'] is None or not (self.home / 'spawn' / (person['spawn_id'] + '.json')).is_file():
            return {'kind': 'no_web_record', 'reason': '没有启动记录，不能恢复原生会话；请先叫醒它，或在原工具中输入 /compact',
                    'running': False}
        # Idle owned conversations can stay resident. Only this fixed maintenance
        # command runs; maintenance_held blocks business input for the whole person.
        claude, codex = self._natives(conn, sid, [other for other in keys if not chat.running(other)])
        for other in codex:
            try:
                lease = codex_runtime.RuntimeLease(self.home, other).acquire()
            except board.Conflict:
                if other == key:
                    return {'kind': 'external', 'reason': 'Codex 会话仍由外部宿主持有或原生进程仍在运行', 'running': False}
                return {'kind': 'identity_in_use', 'running': False,
                        'reason': '此人员的%s仍由外部 Codex 宿主持有' % self._label(conn, other, sid)}
            except (OSError, ValueError) as exc:
                return {'kind': 'ownership_unknown', 'reason': '无法确认 Codex 进程状态：' + _describe(exc), 'running': False}
            lease.release()
        if claude:
            live = agents.get()
            if live is None:
                return {'kind': 'ownership_unknown', 'reason': agents.error, 'running': False}
            for agent in live:
                owner = claude.get(agent['sessionId'])
                if owner == key:
                    return {'kind': 'external', 'reason': '这段 Claude 会话在 VS Code 或终端中运行；请在那边输入 /compact',
                            'running': False}
                if owner is not None:
                    return {'kind': 'identity_in_use', 'running': False,
                            'reason': '此人员的%s在 VS Code 或终端中运行' % self._label(conn, owner, sid)}
            if execution is not None and execution['runtime'] == 'claude' and chat.goal_controller.live_native is None:
                return {'kind': 'ownership_unknown', 'reason': 'Claude 进程状态尚未确认，稍后重试', 'running': False}
        return {'kind': None, 'reason': '已停止，压缩时临时恢复，完成后停止', 'running': False}

    def department_preview(self, project, leader_session_id):
        with self.chat.lock, closing(store.connect(self.home)) as conn:
            leader, group, members = _department(conn, project, leader_session_id)
            agents, people, contexts = _Agents(self.chat), [], {}
            for person in [leader, *members]:
                entries = []
                for context in self._contexts(conn, project, person):
                    state = self._check(conn, context, agents)
                    entries.append({**context, 'available': state['kind'] is None, 'kind': state['kind'],
                                    'reason': state['reason'], 'running': state['running']})
                    contexts[context['target']] = context
                people.append({'session_id': person['session_id'], 'name': person['crew_name'], 'role': person['role'],
                               'contexts': entries})
        identity = self._remember('department', project, {'leader': leader_session_id, 'contexts': contexts,
                                                          'order': list(contexts)})
        return {'preview_id': identity, 'expires_in': PREVIEW_TTL, 'project': project, 'group': group['name'],
                'leader': {'session_id': leader['session_id'], 'name': leader['crew_name']}, 'people': people,
                'total': len(contexts), 'available': sum(entry['available'] for person in people for entry in person['contexts'])}

    def department_start(self, project, leader_session_id, preview_id, targets):
        if (not isinstance(targets, list) or not targets or any(not isinstance(target, str) for target in targets)
                or len(set(targets)) != len(targets)):
            raise board.BoardError('请至少选择一段上下文')
        with closing(store.connect(self.home)) as conn:
            _department(conn, project, leader_session_id)
            existing = self._existing(conn, project, 'department_compact', preview_id)
            if existing is not None:
                self._department_replay(existing, leader_session_id, targets)
                return existing
            preview = self._preview('department', project, preview_id)
            if preview['leader'] != leader_session_id:
                raise board.Conflict('预览不属于这个部门，请重新预览')
            unknown = [target for target in targets if target not in preview['contexts']]
            if unknown:
                raise board.BoardError('选择了预览中没有的上下文')
            selected = [preview['contexts'][target] for target in preview['order'] if target in targets]
            items = [{'target': context['target'], 'session_id': context['session_id'], 'execution_id': context['execution_id'],
                      'label': '%s · %s' % (context['person'], context['context']), 'detail': context} for context in selected]
            identity, created = self._create(conn, project, 'department_compact', preview_id, items,
                                             {'leader_session_id': leader_session_id}, leader_session_id)
            if created:
                self._start_worker(self._run_department, identity, project, leader_session_id,
                                   [{**item, 'position': position} for position, item in enumerate(items)])
            return job(conn, project, identity)

    def _run_department(self, job_id, project, leader_session_id, items):
        try:
            people = {}
            for item in items:
                people.setdefault(item['session_id'], []).append(item)
            threads = [threading.Thread(target=self._run_person, args=(job_id, project, leader_session_id, entries),
                                        name='crew-maintenance-person', daemon=True) for entries in people.values()]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self._finish(job_id)
        except Exception as exc:
            # Worker boundary: the job fails visibly with the real error type.
            self._finish(job_id, type(exc).__name__, str(exc))

    def _run_person(self, job_id, project, leader_session_id, items):
        """One person's contexts in order, so one identity never runs two maintenance natives."""
        remaining = list(items)
        try:
            while remaining:
                item = remaining[0]
                position = item['position']
                self._item(job_id, position, 'running')
                try:
                    detail = self._compact(job_id, project, leader_session_id, item)
                except ItemFailure as failure:
                    self._item(job_id, position, 'failed', failure.kind, failure.reason,
                               self._merge_detail(job_id, position, failure.detail) if failure.detail else None)
                else:
                    self._item(job_id, position, 'completed', detail=self._merge_detail(job_id, position, detail))
                remaining.pop(0)
        except Exception as exc:
            # Worker boundary: this and the remaining items of the person fail with the real error type.
            for item in remaining:
                self._item(job_id, item['position'], 'failed', type(exc).__name__, str(exc))

    def _compact(self, job_id, project, leader_session_id, item):
        chat, key = self.chat, item['target']
        marker = {'job': job_id, 'position': item['position'], 'owned': False}
        with chat.work(background=True) as admitted:
            if not admitted:
                from . import chat as chat_module
                raise ItemFailure('update_paused', chat_module.PAUSED_FOR_UPDATE)
            with chat.lock:
                if chat.closed or self.stopping.is_set():
                    raise ItemFailure('interrupted', '服务正在关闭，没有开始压缩')
                with closing(store.connect(self.home)) as conn:
                    try:
                        leader, _, members = _department(conn, project, leader_session_id)
                    except board.BoardError as exc:
                        raise ItemFailure('scope_changed', str(exc)) from exc
                    if item['session_id'] not in {person['session_id'] for person in [leader, *members]}:
                        raise ItemFailure('scope_changed', '此人员已不在部门中')
                    execution = goals.get_execution(conn, key) if item['execution_id'] else None
                    state = self._check(conn, item, _Agents(chat))
                if state['kind'] is not None:
                    raise ItemFailure(state['kind'], state['reason'])
                if state['running']:
                    current = chat.processes[key]
                    current.maintenance = marker
                else:
                    marker['owned'] = True
                    try:
                        current = chat.maintenance_launch(item['session_id'], execution, marker)
                    except LAUNCH_ERRORS as exc:
                        leftover = chat.processes.get(key)
                        if getattr(leftover, 'maintenance', None) is marker:
                            self._release(key, leftover, {})
                        raise ItemFailure('launch_failed', '恢复原生会话失败：' + _describe(exc)) from exc
                try:
                    start = chat.maintenance_compact(key, 'maintenance:%s:%s' % (job_id, item['position']))
                except LAUNCH_ERRORS as exc:
                    self._release(key, current, {})
                    raise ItemFailure('native_error', '原生压缩请求被拒绝：' + _describe(exc)) from exc
        result, failure = {'process': 'temporary' if marker['owned'] else 'existing'}, None
        try:
            result.update(self._await(current, start))
        except ItemFailure as exc:
            failure = exc
            raise
        finally:
            # Success, a classified failure or an unexpected error all release the native.
            self._release(key, current, result, failure)
        return result

    def _release(self, key, current, result, failure=None):
        try:
            self.chat.maintenance_release(key, current)
        except (board.BoardError, OSError, subprocess.SubprocessError) as exc:
            prefix = failure.reason + '；' if failure is not None else '压缩已原生确认，但' if 'pre_tokens' in result else ''
            raise ItemFailure('stop_failed', prefix + '本次临时启动的原生进程未能确认停止：' + _describe(exc), result) from exc

    def _await(self, current, start):
        journal = current.journal
        claude = getattr(current, 'runtime', 'claude') == 'claude'
        deadline, ended, errors, seq = time.monotonic() + COMPACT_TIMEOUT, None, [], start
        while True:
            if self.stopping.is_set():
                raise ItemFailure('interrupted', '服务正在关闭，压缩没有得到原生确认')
            with journal.condition:
                journal.refresh()
                if len(journal.events) <= seq:
                    journal.condition.wait(POLL_SECONDS)
                events = list(journal.events[seq:])
            for event in events:
                seq = event['seq']
                kind = event['type']
                if kind == 'compact':
                    return {'pre_tokens': event['pre_tokens'], 'post_tokens': event['post_tokens'], 'compacted_at': event['at']}
                if kind == 'compact_cancelled':
                    raise ItemFailure('native_error', event['reason'])
                if kind == 'error' and not event.get('retrying'):
                    errors.append(event['text'])
                if kind == 'turn_result':
                    if not event['success']:
                        raise ItemFailure('native_error', '；'.join(errors) or '原生压缩轮次未成功结束：%s' % event['status'])
                    ended = ended or time.monotonic()
                if kind == 'status' and event['status'] == 'offline':
                    raise ItemFailure('process_exited', '原生进程在压缩完成前退出' + ('：' + '；'.join(errors) if errors else ''))
                if kind == 'status' and event['status'] == 'idle' and claude:
                    if errors:
                        raise ItemFailure('native_error', '；'.join(errors))
                    ended = ended or time.monotonic()
            now = time.monotonic()
            if ended is not None and now - ended >= CONFIRM_GRACE:
                raise ItemFailure('unconfirmed', '原生请求已结束，但没有收到压缩完成事件；未标记为完成')
            if now >= deadline:
                raise ItemFailure('timeout', '%d 秒内没有收到原生压缩完成事件' % COMPACT_TIMEOUT)

    # ------------------------------------------------------------------ workspace cleanup

    def _snapshot(self, conn, project):
        boundary = board.cleanup_boundary(conn, project)
        sessions = [dict(row) for row in conn.execute('SELECT * FROM sessions WHERE project=? ORDER BY role,crew_name,session_id',
                                                      (project,))]
        active = [s for s in sessions if s['status'] != 'closed' and not board.is_archived(conn, s['session_id'])]
        # Archived or unadopted identities still holding a Crew-owned native are stopped too.
        owned = [s for s in sessions if not (s in active and s['crew_name'])
                 and any(self.chat.running(key) for key in self.chat._identity_keys(conn, s['session_id']))]
        groups = [dict(row) for row in conn.execute('SELECT name,leader_session_id FROM groups WHERE project=? AND closed_at IS NULL '
                                                    'ORDER BY name', (project,))]
        unfinished = [dict(row) for row in conn.execute(
            "SELECT id,title,status,leader_session_id FROM goals WHERE project=? AND status NOT IN ('completed','failed','cancelled') "
            "ORDER BY created_at,id", (project,))]

        def ids(sql, *args):
            return [row[0] for row in conn.execute(sql, args)]

        maximum = lambda table: conn.execute('SELECT COALESCE(MAX(id),0) FROM %s WHERE project=?' % table, (project,)).fetchone()[0]
        contexts = ids("SELECT id FROM goal_context_items WHERE state='active' AND ((scope IN ('project','leader') AND project=?) "
                       "OR (scope='goal' AND goal_id IN (SELECT id FROM goals WHERE project=?))) ORDER BY id", project, project)
        config = board.git_config(conn, project)
        return {
            'boundary': boundary, 'sessions': sessions, 'active': active,
            'owned': owned, 'groups': groups, 'goals': unfinished,
            'channel_max': maximum('channel_messages'), 'digest_max': maximum('channel_digests'), 'handoff_max': maximum('handoffs'),
            'contexts': contexts,
            'todos': ids("SELECT id FROM todos WHERE project=? AND status='open' ORDER BY id", project),
            'decisions': ids("SELECT id FROM decisions WHERE project=? AND status='open' ORDER BY id", project),
            'claims': ids('SELECT id FROM claims WHERE project=? AND released_at IS NULL ORDER BY id', project),
            'leases': ids('SELECT id FROM leases WHERE project=? AND released_at IS NULL ORDER BY id', project),
            'git_admin': config['admin_session_id'] if config is not None else None,
        }

    @staticmethod
    def _fingerprint(root, snapshot):
        """The confirmed scope: who is archived or stopped and which goals are cancelled.

        Channel posts, handoffs, context items and open work that appear after
        the preview are older than the cleanup too, so they move behind the same
        boundary; a different set of people, departments or goals needs a new preview.
        """
        value = {'root': root, 'boundary': snapshot['boundary'], 'git_admin': snapshot['git_admin'],
                 'groups': snapshot['groups'], 'goals': [goal['id'] for goal in snapshot['goals']],
                 'active': [s['session_id'] for s in snapshot['active']],
                 'owned': [s['session_id'] for s in snapshot['owned']]}
        return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()

    def cleanup_preview(self, project):
        chat = self.chat
        with chat.lock, closing(store.connect(self.home)) as conn:
            info = board.project(conn, project)
            snapshot = self._snapshot(conn, project)
            boundary = snapshot['boundary']
            agents, people = _Agents(chat), []
            group_leaders = {group['leader_session_id']: group['name'] for group in snapshot['groups']}
            for session in {s['session_id']: s for s in [*snapshot['active'], *snapshot['owned']]}.values():
                sid = session['session_id']
                keys = chat._identity_keys(conn, sid)
                claude, _ = self._natives(conn, sid, keys)
                external = None
                if claude and session['crew_name']:
                    live = agents.get()
                    external = None if live is None else any(agent['sessionId'] in claude for agent in live)
                people.append({'session_id': sid, 'name': session['crew_name'] or session['cc_name'] or sid,
                               'role': 'git' if sid == snapshot['git_admin'] else session['role'] if session['crew_name'] else 'pending',
                               'group': session['group_name'], 'archived': session not in snapshot['active'],
                               'running': any(chat.running(key) for key in keys), 'busy': any(chat.busy(key) for key in keys),
                               'external': external, 'managed': session['crew_name'] is not None})
            count = lambda sql, *args: conn.execute(sql, args).fetchone()[0]
            label = conn.execute('SELECT display_name FROM project_settings WHERE project=?', (project,)).fetchone()
            preview = {
                'project': {'name': info['name'], 'root': info['root'], 'display_name': label['display_name'] if label else None},
                'departments': [{'name': group['name'], 'leader_session_id': group['leader_session_id'],
                                 'members': sum(1 for s in snapshot['active'] if s['group_name'] == group['name']
                                                and s['session_id'] != group['leader_session_id'])}
                                for group in snapshot['groups']],
                'people': people,
                'identities': {'total': len(snapshot['active']),
                               'leaders': sum(1 for s in snapshot['active'] if s['role'] == 'leader' and s['crew_name']),
                               'members': sum(1 for s in snapshot['active'] if s['role'] == 'member' and s['crew_name']),
                               'pending': sum(1 for s in snapshot['active'] if not s['crew_name']),
                               'git_admin': snapshot['git_admin'] is not None},
                'goals': {'unfinished': [{'id': goal['id'], 'title': goal['title'], 'status': goal['status']} for goal in snapshot['goals']],
                          'finished': count("SELECT COUNT(*) FROM goals WHERE project=? AND status IN ('completed','failed','cancelled')", project)},
                'channel': {'messages': count('SELECT COUNT(*) FROM channel_messages WHERE project=? AND id>?', project, boundary['channel_after']),
                            'digests': count('SELECT COUNT(*) FROM channel_digests WHERE project=? AND id>?', project, boundary['digest_after'])},
                'handoffs': count('SELECT COUNT(*) FROM handoffs WHERE project=? AND id>?', project, boundary['handoff_after']),
                'context': len(snapshot['contexts']), 'todos': len(snapshot['todos']), 'decisions': len(snapshot['decisions']),
                'claims': len(snapshot['claims']), 'leases': len(snapshot['leases']),
                'running': {'processes': sum(person['running'] for person in people), 'busy': sum(person['busy'] for person in people),
                            'external': sum(person['external'] is True for person in people),
                            'unknown': agents.error},
                'kept': {'documents': count('SELECT COUNT(*) FROM documents WHERE project=?', project),
                         'reports': count('SELECT COUNT(*) FROM goal_reports r JOIN goals g ON g.id=r.goal_id WHERE g.project=?', project),
                         'finished_goals': count("SELECT COUNT(*) FROM goals WHERE project=? AND status IN ('completed','failed','cancelled')", project),
                         'crew_rules': count("SELECT COUNT(*) FROM goal_context_items WHERE scope='crew' AND state='active'")},
                'cleanups': boundary['cleanups'],
            }
            preview['empty'] = not (snapshot['active'] or snapshot['groups'] or snapshot['goals'] or preview['channel']['messages']
                                    or preview['channel']['digests'] or preview['handoffs'] or snapshot['contexts'] or snapshot['todos']
                                    or snapshot['decisions'] or snapshot['claims'] or snapshot['leases'] or snapshot['owned'])
            running = conn.execute("SELECT kind FROM maintenance_jobs WHERE project=? AND status='running'", (project,)).fetchone()
            preview['blocked'] = None if running is None else ('工作区清理正在进行' if running['kind'] == 'workspace_cleanup'
                                                               else '部门压缩正在进行，完成后再清理')
            fingerprint = self._fingerprint(store.norm_path(info['root']), snapshot)
            items = ([{'target': 'session:' + person['session_id'], 'session_id': person['session_id'],
                       'label': '停止「%s」的托管执行' % person['name'], 'detail': {'name': person['name']}}
                      for person in people if person['managed'] or person['running']]
                     + [{'target': 'goal:' + goal['id'], 'label': '取消目标「%s」' % goal['title'][:60],
                         'detail': {'goal_id': goal['id'], 'title': goal['title']}} for goal in snapshot['goals']]
                     + [{'target': 'archive', 'label': '归档部门与身份，旧交流、交接与上下文退出后续装载', 'detail': {}}])
        preview['preview_id'] = self._remember('cleanup', project, {'root': store.norm_path(info['root']), 'fingerprint': fingerprint,
                                                                    'items': items, 'summary': {key: preview[key] for key in (
                                                                        'departments', 'identities', 'goals', 'channel', 'handoffs',
                                                                        'context', 'todos', 'decisions', 'claims', 'leases', 'kept')},
                                                                    'empty': preview['empty']})
        preview['expires_in'] = PREVIEW_TTL
        return preview

    def cleanup_start(self, project, preview_id, confirm):
        if confirm is not True:
            raise board.BoardError('清理需要明确确认“归档旧工作并重新开始”')
        with closing(store.connect(self.home)) as conn:
            board.project(conn, project)
            existing = self._existing(conn, project, 'workspace_cleanup', preview_id)
            if existing is not None:
                return existing
            preview = self._preview('cleanup', project, preview_id)
            if preview['empty']:
                raise board.BoardError('当前工作区没有可清理的旧工作')
            with self.chat.lock:
                current = self._fingerprint(store.norm_path(board.project(conn, project)['root']), self._snapshot(conn, project))
            if current != preview['fingerprint']:
                raise board.Conflict('工作区在预览后发生了变化，请重新预览')
            identity, created = self._create(conn, project, 'workspace_cleanup', preview_id, preview['items'],
                                             {'preview': preview['summary']})
            if created:
                self._start_worker(self._run_cleanup, identity, project, preview)
            return job(conn, project, identity)

    def _run_cleanup(self, job_id, project, preview):
        try:
            self._cleanup(job_id, project, preview)
        except Exception as exc:
            # Worker boundary: real partial state stays; the job fails with the real error type.
            self._finish(job_id, type(exc).__name__, str(exc))

    def _cleanup(self, job_id, project, preview):
        chat, positions = self.chat, {item['target']: position for position, item in enumerate(preview['items'])}
        with chat.work(background=True) as admitted:
            if not admitted:
                from . import chat as chat_module
                self._finish(job_id, 'update_paused', chat_module.PAUSED_FOR_UPDATE)
                return
            # The service write lock and the process lock keep every other model
            # start, delivery and digest out until the boundary is committed.
            with self.service.write_lock, chat.lock, closing(store.connect(self.home)) as conn:
                info = board.project(conn, project)
                snapshot = self._snapshot(conn, project)
                if self._fingerprint(store.norm_path(info['root']), snapshot) != preview['fingerprint']:
                    self._finish(job_id, 'preview_changed', '工作区在预览后发生了变化，没有执行；请重新预览')
                    return
                goal_ids = [goal['id'] for goal in snapshot['goals']]
                for goal_id in goal_ids:
                    goals.request_cancel(conn, goal_id)
                failed = False
                for item in preview['items']:
                    if not item['target'].startswith('session:'):
                        continue
                    position = positions[item['target']]
                    self._item(job_id, position, 'running')
                    try:
                        stopped = chat.stop_identity(item['session_id'])
                    except board.Conflict as exc:
                        failed = True
                        self._item(job_id, position, 'failed', 'external', str(exc))
                    except (board.BoardError, OSError, codex_client.CodexError, subprocess.SubprocessError) as exc:
                        failed = True
                        self._item(job_id, position, 'failed', 'stop_failed', _describe(exc))
                    else:
                        self._item(job_id, position, 'completed', detail={**item['detail'], 'executions': stopped['execution_ids']})
                for goal_id in goal_ids:
                    position = positions['goal:' + goal_id]
                    chat.goal_controller.sync(conn, goal_id)
                    status = goals.get(conn, goal_id)['status']
                    if status == 'cancelled':
                        self._item(job_id, position, 'completed')
                    else:
                        failed = True
                        self._item(job_id, position, 'failed', 'not_confirmed',
                                   '已请求取消，目标仍为 %s，等待原生执行确认停止' % status)
                position = positions['archive']
                if failed:
                    self._item(job_id, position, 'failed', 'blocked',
                               '有身份未确认停止或目标未确认取消；没有归档，也没有建立清理边界。处理后请重新预览')
                    self._finish(job_id)
                    return
                self._item(job_id, position, 'running')
                with board.transaction(conn):
                    self._archive(conn, project, job_id)
                with self.service.channel_digest.lock:
                    self.service.channel_digest.errors.pop(project, None)
                    self.service.channel_digest.failed_upto.pop(project, None)

    def _archive(self, conn, project, job_id):
        """One transaction: archive identities, withdraw open work and move the boundaries."""
        chat, now = self.chat, store.now_iso()
        value = job(conn, project, job_id)
        groups = [dict(row) for row in conn.execute('SELECT * FROM groups WHERE project=? ORDER BY name', (project,))]
        notes = [{'name': group['name'], 'leader_session_id': group['leader_session_id'], 'note': group['note']}
                 for group in groups if group['note'] is not None]
        for row in conn.execute('SELECT session_id FROM sessions WHERE project=?', (project,)).fetchall():
            for key in chat._identity_keys(conn, row['session_id']):
                if chat.running(key):
                    raise board.Conflict('仍有托管进程在运行，没有归档：' + key)
        if conn.execute("SELECT 1 FROM goals WHERE project=? AND status NOT IN ('completed','failed','cancelled')", (project,)).fetchone():
            raise board.Conflict('仍有目标未确认取消，没有归档')
        counts = {'git_admin': False, 'departments': sum(group['closed_at'] is None for group in groups),
                  'identities': conn.execute("SELECT COUNT(*) FROM sessions s LEFT JOIN session_controls c ON c.session_id=s.session_id "
                                            "WHERE s.project=? AND s.status<>'closed' AND "
                                            "(c.session_id IS NULL OR (c.archived_status IS NULL AND c.removed=0))", (project,)).fetchone()[0]}
        config = board.git_config(conn, project)
        if config is not None and config['admin_session_id'] is not None:
            git_admin._disable(conn, project, now)
            counts['git_admin'] = True
        for group in conn.execute('SELECT name FROM groups WHERE project=? AND closed_at IS NULL ORDER BY name', (project,)).fetchall():
            session_ops._close_group(conn, project, group['name'], True, board.HUMAN, now)
        for session in [dict(row) for row in conn.execute("SELECT * FROM sessions WHERE project=? AND status<>'closed'", (project,))]:
            if not board.is_archived(conn, session['session_id']):
                session_ops._archive(conn, session, False, now)
        conn.execute('UPDATE groups SET note=NULL WHERE project=?', (project,))
        note = '工作区清理：旧工作已归档'
        counts['todos'] = conn.execute("UPDATE todos SET status='cancelled',note=CASE WHEN note IS NULL OR note='' THEN ? "
                                       "ELSE note||char(10)||? END,resolved_by=?,resolved_at=?,updated_at=?,version=version+1 "
                                       "WHERE project=? AND status='open'", (note, note, board.HUMAN, now, now, project)).rowcount
        conn.execute("DELETE FROM decision_holds WHERE decision_id IN (SELECT id FROM decisions WHERE project=? AND status='open')", (project,))
        counts['decisions'] = conn.execute("UPDATE decisions SET status='withdrawn' WHERE project=? AND status='open'", (project,)).rowcount
        counts['claims'] = conn.execute('UPDATE claims SET released_at=? WHERE project=? AND released_at IS NULL', (now, project)).rowcount
        counts['leases'] = conn.execute('UPDATE leases SET released_at=?,released_by=?,release_note=? WHERE project=? AND released_at IS NULL',
                                        (now, board.HUMAN, note, project)).rowcount
        contexts = [row[0] for row in conn.execute(
            "SELECT id FROM goal_context_items WHERE state='active' AND ((scope IN ('project','leader') AND project=?) "
            "OR (scope='goal' AND goal_id IN (SELECT id FROM goals WHERE project=?))) ORDER BY id", (project, project))]
        for item_id in contexts:
            if goal_context.get(conn, item_id)['state'] == 'active':
                goal_context.invalidate(conn, item_id, note, now=now)
        counts['context'] = len(contexts)
        old = board.cleanup_boundary(conn, project)
        latest = {name: conn.execute('SELECT COALESCE(MAX(id),0) FROM %s WHERE project=?' % table, (project,)).fetchone()[0]
                  for name, table in (('channel', 'channel_messages'), ('digest', 'channel_digests'), ('handoff', 'handoffs'))}
        boundary = {name: {'after': old[name + '_after'], 'upto': max(old[name + '_after'], latest[name])} for name in latest}
        conn.execute('INSERT INTO workspace_cleanup_boundaries(project,channel_after,digest_after,handoff_after,cleanups,job_id,updated_at) '
                     'VALUES(?,?,?,?,1,?,?) ON CONFLICT(project) DO UPDATE SET channel_after=excluded.channel_after,'
                     'digest_after=excluded.digest_after,handoff_after=excluded.handoff_after,'
                     'cleanups=workspace_cleanup_boundaries.cleanups+1,job_id=excluded.job_id,updated_at=excluded.updated_at',
                     (project, boundary['channel']['upto'], boundary['digest']['upto'], boundary['handoff']['upto'], job_id, now))
        for name, table in (('channel', 'channel_messages'), ('handoffs', 'handoffs')):
            span = boundary['handoff' if name == 'handoffs' else name]
            counts[name] = conn.execute('SELECT COUNT(*) FROM %s WHERE project=? AND id>? AND id<=?' % table,
                                        (project, span['after'], span['upto'])).fetchone()[0]
        board._event(conn, 'workspace:cleanup', project, board.HUMAN, {'job_id': job_id, **counts}, now)
        # The archive boundary, readable history and successful receipt commit
        # together. A restart cannot leave a cleared workspace with no receipt.
        detail = {**value['detail'], 'boundary': boundary, 'result': counts, 'archived_groups': notes}
        conn.execute("UPDATE maintenance_items SET status='completed',detail_json=?,updated_at=? "
                     "WHERE job_id=? AND target='archive'", (_json(counts), now, job_id))
        conn.execute("UPDATE maintenance_jobs SET status='completed',detail_json=?,updated_at=?,finished_at=? WHERE id=?",
                     (_json(detail), now, now, job_id))
        return {'counts': counts, 'boundary': boundary}
