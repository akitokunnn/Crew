"""Legacy home recovery exercises real SQLite stores and journal files."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from crew import board, maintenance, store, workspace_recovery as recovery
from crew.service_lock import HomeServiceLock
from crew.storage_service import StorageService


STAMP = '2026-10-24T20:00:00.000Z'


class WorkspaceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='crew-recovery-test-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / 'legacy'
        self.target = self.base / 'stable'
        self.workspace = self.base / 'workspace'
        self.workspace.mkdir()
        self.seed(self.source, 'old', 'leader', self.workspace)
        self.seed(self.target, 'MIB', 'mib-leader', self.base / 'MIB')
        (self.workspace / 'report.md').write_text('historical output', encoding='utf-8')
        self.files(self.source, 'leader')

    def insert(self, conn, table, **row):
        conn.execute('INSERT INTO ' + table + '(' + ','.join(row) + ') VALUES(' +
                     ','.join('?' for _ in row) + ')', tuple(row.values()))

    def test_reply_links_and_deduplication_survive_recovery(self):
        from crew import goal_messages, goals
        with contextlib.closing(store.connect(self.source)) as conn:
            board.join(conn,'peer-chief','old','peer',self.workspace,self.workspace,'main',role='leader')
            goal = goals.create(conn,'old','leader','Cross-group request','Keep its original scope',['Evidence'])
            goals.transition(conn,goal['id'],'running')
            execution = goals.create_execution(conn,goal['id'],'leader',runtime='claude')
            request = goal_messages.queue(conn,'leader','peer-chief','Original interface request',execution['id'],kind='decision')
            reply = goal_messages.queue(conn,'peer-chief','leader','Original reply',kind='result',reply_to=request['message_id'])
        recovery.import_workspace(self.source,self.target,'old')
        with contextlib.closing(store.connect(self.target)) as conn:
            original = conn.execute("SELECT * FROM peer_messages WHERE project='old' AND body='Original interface request'").fetchone()
            response = conn.execute("SELECT * FROM peer_messages WHERE project='old' AND body='Original reply'").fetchone()
            self.assertEqual(original['id'],request['message_id'])
            self.assertEqual(response['reply_to'],original['id'])
            meta = conn.execute('SELECT * FROM goal_message_meta WHERE message_id=?',(response['id'],)).fetchone()
            self.assertEqual(meta['causation_id'],original['id'])
            repeated = goal_messages.queue(conn,response['sender_session_id'],response['target_session_id'],response['body'],
                                           kind='result',reply_to=original['id'],message_id='same-reply-after-recovery')
            self.assertEqual(repeated['status'],'archived')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_frozen_notice_batch_remaps_notice_ids_but_keeps_unknown_native_identity(self):
        from crew import chat
        with contextlib.closing(store.connect(self.source)) as conn:
            board.queue_leader_notice(conn,'leader','Pending source notice','crew')
            notice = conn.execute("SELECT id FROM leader_notices WHERE text='Pending source notice'").fetchone()[0]
        journal = chat.Journal(self.source/'chat'/'leader.jsonl')
        journal.emit('leader_notice_batch',id='frozen-native-id',notice_ids=[notice],text='Original frozen input')
        journal.emit('input_delivery',message_id='frozen-native-id',status='unknown',digest='retained-digest')
        recovery.import_workspace(self.source,self.target,'old')
        with contextlib.closing(store.connect(self.target)) as conn:
            current = conn.execute("SELECT id FROM leader_notices WHERE project='old' AND text='Pending source notice'").fetchone()[0]
        restored = chat.Journal(self.target/'chat'/'leader.jsonl')
        batch = next(event for event in restored.events if event['type']=='leader_notice_batch')
        self.assertNotEqual(current,notice)
        self.assertEqual(batch['notice_ids'],[current])
        self.assertEqual(batch['id'],'frozen-native-id')
        self.assertEqual(batch['text'],'Original frozen input')
        self.assertEqual(restored.inputs['frozen-native-id']['status'],'unknown')

    def test_newer_preferences_maintenance_and_cleanup_boundaries_keep_their_scope(self):
        with contextlib.closing(store.connect(self.source)) as conn:
            self.insert(conn, 'ai_task_settings', purpose='leader', selection_json='{"runtime":"claude"}', updated_at=STAMP)
            self.insert(conn, 'leader_member_settings', leader_session_id='leader', selection_json='{"runtime":"codex"}', updated_at=STAMP)
            self.insert(conn, 'maintenance_jobs', id='old-job', project='old', kind='workspace_cleanup',
                        request_id='request', status='running', detail_json='{}', created_at=STAMP, updated_at=STAMP)
            self.insert(conn, 'maintenance_items', job_id='old-job', position=0, target='leader', session_id='leader',
                        label='PM', status='running', detail_json='{}', updated_at=STAMP)
            boundary = conn.execute('SELECT MAX(id) FROM channel_messages').fetchone()[0]
            self.insert(conn, 'workspace_cleanup_boundaries', project='old', channel_after=boundary, digest_after=0,
                        handoff_after=0, cleanups=1, job_id='old-job', updated_at=STAMP)
            conn.execute('UPDATE maintenance_jobs SET detail_json=? WHERE id=?',
                         (json.dumps({'boundary':{'channel':{'after':0,'upto':boundary},'digest':{'after':0,'upto':0},'handoff':{'after':0,'upto':0}}}), 'old-job'))
        recovery.import_workspace(self.source, self.target, 'old')
        with contextlib.closing(store.connect(self.target)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM ai_task_settings').fetchone()[0], 0)
            self.assertEqual(json.loads(conn.execute('SELECT selection_json FROM leader_member_settings WHERE leader_session_id=?', ('leader',)).fetchone()[0]), {'runtime':'codex'})
            self.assertEqual(conn.execute('SELECT status FROM maintenance_jobs WHERE id=?', ('old-job',)).fetchone()[0], 'interrupted')
            self.assertEqual(conn.execute('SELECT status FROM maintenance_items WHERE job_id=?', ('old-job',)).fetchone()[0], 'interrupted')
            after = conn.execute('SELECT channel_after FROM workspace_cleanup_boundaries WHERE project=?', ('old',)).fetchone()[0]
            self.assertEqual(conn.execute('SELECT project FROM channel_messages WHERE id=?', (after,)).fetchone()[0], 'old')
            self.assertNotEqual(after, boundary)
            archived=maintenance.archive(conn,'old','old-job','channel')
            self.assertEqual([row['body'] for row in archived['messages']], ['old channel message'])

    def seed(self, home, name, sid, root):
        root.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(store.connect(home)) as c:
            board.init_project(c, name, root, now=STAMP)
            self.insert(c, 'sessions', session_id=sid, project=name, crew_name='PM',
                        role='leader', group_name='PM', status='offline', cwd=str(root),
                        spawn_id='spawn-' + sid, started_at=STAMP)
            self.insert(c, 'groups', project=name, name='PM', leader_session_id=sid, created_at=STAMP)
            self.insert(c, 'session_runtimes', session_id=sid, runtime='claude', native_id='native-' + sid, updated_at=STAMP)
            self.insert(c, 'session_runtime_bindings', session_id=sid, runtime='claude',
                        native_id='native-' + sid, updated_at=STAMP)
            self.insert(c, 'reports', project=name, session_id=sid, body='finished report', created_at=STAMP)
            self.insert(c, 'decisions', project=name, session_id=sid, question='question', status='answered',
                        created_at=STAMP, answered_at=STAMP, delivered_at=STAMP)
            self.insert(c, 'decision_briefs', decision_id=c.execute('SELECT MAX(id) FROM decisions').fetchone()[0],
                        title='choice', context='context', why_user='reason', recommendation_reason='reason',
                        impact='impact', options_json='[]', updated_at=STAMP)
            self.insert(c, 'leader_notices', project=name, session_id=sid, source='crew', text='old notice',
                        created_at=STAMP, sent_at=STAMP)
            notice = c.execute('SELECT MAX(id) FROM leader_notices').fetchone()[0]
            self.insert(c, 'channel_messages', project=name, speaker_session_id=sid,
                        speaker_name='PM', body='old channel message', created_at=STAMP)
            message = c.execute('SELECT MAX(id) FROM channel_messages').fetchone()[0]
            self.insert(c, 'channel_mentions', message_id=message, session_id=sid, name='PM', notice_id=notice)
            self.insert(c, 'channel_reads', project=name, reader='user', message_id=message, updated_at=STAMP)
            report = c.execute('SELECT MAX(id) FROM reports').fetchone()[0]
            self.insert(c, 'documents', project=name, session_id=sid, leader_session_id=sid,
                        submitter='PM', group_name='PM', path=str(root / 'report.md'),
                        path_key=store.norm_path(root / 'report.md'), title='report', kind='report',
                        source='report', source_seq=report, created_at=STAMP, updated_at=STAMP)
            self.insert(c, 'events', project=name, session_id=sid, kind='report', ts=STAMP,
                        detail=json.dumps({'id': report, 'round': 1}))
            self.insert(c, 'document_scans', project=name, source='reports', offset=report, scanned_at=STAMP)
            self.goal(c, name, sid, 'goal-' + sid, 'execution-' + sid)

    def goal(self, c, project, sid, goal, execution):
        self.insert(c, 'goals', id=goal, project=project, leader_session_id=sid, title='completed goal',
                    original_request='deliver', status='completed', requirement_version=1, current_round=1,
                    created_at=STAMP, updated_at=STAMP)
        self.insert(c, 'goal_rounds', goal_id=goal, number=1, requirement_version=1,
                    status='completed', outcome='completed', opened_at=STAMP, ended_at=STAMP)
        self.insert(c, 'goal_executions', id=execution, goal_id=goal, round=1, session_id=sid, generation=1,
                    runtime='claude', status='completed', requirement_version=1, created_at=STAMP, updated_at=STAMP)
        self.insert(c, 'goal_execution_bindings', execution_id=execution, runtime='claude',
                    native_id='goal-native-' + sid, updated_at=STAMP)
        self.insert(c, 'goal_reports', id='final-' + goal, goal_id=goal, round=1, requirement_version=1,
                    version=1, outcome='completed', data_json='{"summary":"finished"}', html='<p>finished</p>', created_at=STAMP)
        self.insert(c, 'goal_documents', goal_id=goal,
                    document_id=c.execute('SELECT MAX(id) FROM documents').fetchone()[0],
                    execution_id=execution, created_at=STAMP)
        self.insert(c, 'goal_context_items', id='context-' + goal, project=None, goal_id=goal,
                    scope='goal', kind='evidence', version=1, state='active', protected=0,
                    created_at=STAMP, updated_at=STAMP)
        self.insert(c, 'goal_context_versions', item_id='context-' + goal, version=1, text='context',
                    source_json='{}', depends_json='[]', state='active', actor=sid, created_at=STAMP)

    def files(self, home, sid):
        (home / 'chat').mkdir(exist_ok=True)
        message = {'seq': 1, 'type': 'message', 'id': 1, 'role': 'user', 'text': '#1 original report',
                   'source': 'web', 'delivery': {'answers': [1], 'handoffs': []}}
        (home / 'chat' / (sid + '.jsonl')).write_text(json.dumps(message) + '\n', encoding='utf-8')
        (home / 'chat' / ('execution-' + sid + '.jsonl')).write_text(
            '{"seq":1,"type":"status","status":"idle"}\n', encoding='utf-8')
        (home / 'spawn').mkdir(exist_ok=True)
        spec = {'id': 'spawn-' + sid, 'project': 'old', 'md': str(home / 'spawn' / ('spawn-' + sid + '.md'))}
        (home / 'spawn' / ('spawn-' + sid + '.json')).write_text(json.dumps(spec), encoding='utf-8')
        (home / 'spawn' / ('spawn-' + sid + '.md')).write_text('original brief', encoding='utf-8')
        attachments = home / 'chat' / 'files' / sid
        attachments.mkdir(parents=True)
        (attachments / 'image.png').write_bytes(b'picture')

    def snapshot(self, home):
        return {str(p.relative_to(home)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in home.rglob('*') if p.is_file() and p.suffix not in ('.db-shm', '.db-wal')}

    def test_full_graph_recovery_remaps_collisions_and_preserves_source_and_mib(self):
        before = self.snapshot(self.source)
        result = recovery.import_workspace(self.source, self.target, 'old')
        self.assertEqual(result['status'], 'recovered')
        self.assertTrue((Path(result['backup']) / 'crew.db').is_file())
        self.assertEqual(self.snapshot(self.source), before)
        with contextlib.closing(store.connect(self.target)) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sessions WHERE project="MIB"').fetchone()[0], 1)
            report = c.execute('SELECT id FROM reports WHERE project="old"').fetchone()[0]
            self.assertEqual(report, 2)
            doc = c.execute('SELECT id,source_seq FROM documents WHERE project="old"').fetchone()
            self.assertEqual(doc['source_seq'], report)
            self.assertEqual(c.execute('SELECT document_id FROM goal_documents WHERE goal_id="goal-leader"').fetchone()[0], doc['id'])
            self.assertEqual(c.execute('SELECT decision_id FROM decision_briefs ORDER BY decision_id DESC').fetchone()[0], 2)
            mention = c.execute('SELECT message_id,notice_id FROM channel_mentions WHERE session_id="leader"').fetchone()
            self.assertEqual(tuple(mention), (2, 2))
            self.assertEqual(c.execute('SELECT message_id FROM channel_reads WHERE project="old"').fetchone()[0], 2)
            detail = json.loads(c.execute('SELECT detail FROM events WHERE project="old" AND kind="report"').fetchone()[0])
            self.assertEqual(detail, {'id': 2, 'round': 1})
            self.assertEqual(c.execute('SELECT COUNT(*) FROM goal_context_versions WHERE item_id="context-goal-leader"').fetchone()[0], 1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM document_scans WHERE project="old"').fetchone()[0], 0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM session_stop_gates WHERE session_id="leader"').fetchone()[0], 1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM todo_backfills WHERE leader_session_id="leader"').fetchone()[0], 1)
            for table in ('notification_messages', 'lark_notification_messages'):
                self.assertEqual(c.execute('SELECT status FROM ' + table + ' WHERE item_id="report:2"').fetchone()[0], 'archived')
                self.assertEqual(c.execute('SELECT status FROM ' + table + ' WHERE item_id="goal-report:final-goal-leader"').fetchone()[0], 'archived')
            self.assertFalse(c.execute('PRAGMA foreign_key_check').fetchall())
        journal = json.loads((self.target / 'chat' / 'leader.jsonl').read_text(encoding='utf-8'))
        self.assertEqual(journal['seq'], 1)
        self.assertEqual(journal['id'], 1)
        self.assertEqual(journal['text'], '#1 original report')
        self.assertEqual(journal['delivery']['answers'], [2])
        spec = json.loads((self.target / 'spawn' / 'spawn-leader.json').read_text(encoding='utf-8'))
        self.assertEqual(spec['md'], str(self.target / 'spawn' / 'spawn-leader.md'))
        self.assertEqual((self.target / 'chat' / 'files' / 'leader' / 'image.png').read_bytes(), b'picture')

    def test_repeat_start_is_idempotent(self):
        recovery.import_workspace(self.source, self.target, 'old')
        before = self.snapshot(self.target)
        self.assertEqual(recovery.import_workspace(self.source, self.target, 'old')['status'], 'already_recovered')
        self.assertEqual(before, self.snapshot(self.target))

    def test_composite_fk_does_not_import_other_projects_round_one(self):
        self.seed(self.source, 'unrelated', 'other-leader', self.base / 'unrelated')
        recovery.import_workspace(self.source, self.target, 'old')
        with contextlib.closing(store.connect(self.target)) as c:
            self.assertIsNone(c.execute('SELECT 1 FROM goals WHERE id="goal-other-leader"').fetchone())
            self.assertIsNone(c.execute('SELECT 1 FROM goal_executions WHERE id="execution-other-leader"').fetchone())
            self.assertIsNone(c.execute('SELECT 1 FROM goal_reports WHERE goal_id="goal-other-leader"').fetchone())
            self.assertIsNone(c.execute('SELECT 1 FROM sessions WHERE session_id="other-leader"').fetchone())

    def test_same_root_empty_registration_keeps_stable_key(self):
        with contextlib.closing(store.connect(self.target)) as c:
            board.init_project(c, 'existing-key', self.workspace)
        result = recovery.import_workspace(self.source, self.target, 'old')
        self.assertEqual(result['project'], 'existing-key')
        with contextlib.closing(store.connect(self.target)) as c:
            self.assertEqual(c.execute('SELECT project FROM sessions WHERE session_id="leader"').fetchone()[0], 'existing-key')
            self.assertIsNone(c.execute('SELECT 1 FROM projects WHERE name="old"').fetchone())

    def test_populated_same_root_and_same_name_other_root_are_rejected(self):
        with contextlib.closing(store.connect(self.target)) as c:
            c.execute('UPDATE projects SET root=? WHERE name="MIB"', (str(self.workspace),))
        with self.assertRaisesRegex(recovery.RecoveryError, '已有独立历史'):
            recovery.import_workspace(self.source, self.target, 'old')
        with contextlib.closing(store.connect(self.target)) as c:
            c.execute('UPDATE projects SET root=? WHERE name="MIB"', (str(self.base / 'MIB'),))
            board.init_project(c, 'old', self.base / 'different')
        with self.assertRaisesRegex(recovery.RecoveryError, '同名但目录不同'):
            recovery.import_workspace(self.source, self.target, 'old')

    def test_file_conflict_rolls_back_and_preserves_existing_file(self):
        (self.target / 'chat').mkdir()
        conflict = self.target / 'chat' / 'leader.jsonl'
        conflict.write_text('existing data', encoding='utf-8')
        with self.assertRaisesRegex(recovery.RecoveryError, '历史文件与现有文件冲突'):
            recovery.import_workspace(self.source, self.target, 'old')
        self.assertEqual(conflict.read_text(encoding='utf-8'), 'existing data')
        with contextlib.closing(store.connect(self.target)) as c:
            self.assertIsNone(c.execute('SELECT 1 FROM projects WHERE name="old"').fetchone())

    def test_late_copy_failure_rolls_back_database_and_new_files(self):
        original = Path.open
        copied = []

        def open_file(path, mode='r', *args, **kwargs):
            if mode == 'xb' and store.is_under(path, self.target):
                if copied:
                    raise OSError('disk full')
                copied.append(path)
            return original(path, mode, *args, **kwargs)

        with mock.patch.object(Path, 'open', open_file):
            with self.assertRaisesRegex(OSError, 'disk full'):
                recovery.import_workspace(self.source, self.target, 'old')
        self.assertTrue(copied)
        self.assertFalse(copied[0].exists())
        with contextlib.closing(store.connect(self.target)) as c:
            self.assertIsNone(c.execute('SELECT 1 FROM projects WHERE name="old"').fetchone())
            self.assertEqual(c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 1)

    def test_custom_and_isolated_home_do_not_inspect_legacy_sources(self):
        missing = self.base / 'missing-home'
        service = StorageService(self.target, legacy_homes=[missing])
        self.assertEqual(service._homes(), [self.target.resolve()])
        with mock.patch.object(store, 'default_home', return_value=self.target):
            self.assertEqual(StorageService(self.target, isolated=True, legacy_homes=[missing])._homes(), [self.target.resolve()])
        self.assertFalse(missing.exists())

    def test_ambiguous_history_is_presented_for_explicit_selection_without_importing(self):
        other_source = self.base / 'other-legacy'
        self.seed(other_source, 'old-copy', 'copy-leader', self.workspace)
        service = StorageService(self.target, legacy_homes=[self.source, other_source])
        with mock.patch.object(store, 'default_home', return_value=self.target):
            discovery = service.discover(self.workspace)
            self.assertEqual(discovery['status'], 'upgrade_required')
            self.assertEqual({row['project'] for row in discovery['candidates']}, {'old', 'old-copy'})
            with self.assertRaisesRegex(ValueError, '选择'):
                service.open({'root':str(self.workspace), 'action':'upgrade'})
        with contextlib.closing(store.connect(self.target)) as conn:
            self.assertEqual([row['name'] for row in conn.execute('SELECT name FROM projects')], ['MIB'])

    def test_active_source_is_not_copied(self):
        lock = HomeServiceLock(self.source)
        self.assertTrue(lock.acquire())
        self.addCleanup(lock.release)
        with self.assertRaisesRegex(recovery.RecoveryError, '旧版本仍在使用'):
            recovery.import_workspace(self.source, self.target, 'old')

    def test_unknown_nonempty_table_fails_without_silently_losing_history(self):
        with contextlib.closing(store.connect(self.source)) as c:
            c.execute('CREATE TABLE future_history(payload TEXT)')
            c.execute('INSERT INTO future_history VALUES ("unknown")')
        with self.assertRaisesRegex(recovery.RecoveryError, '尚不支持恢复的表'):
            recovery.import_workspace(self.source, self.target, 'old')

    def test_active_goals_are_paused_and_terminal_goals_are_preserved(self):
        with contextlib.closing(store.connect(self.source)) as c:
            c.execute('UPDATE goals SET status="running"')
            c.execute('UPDATE goal_executions SET status="running"')
            c.execute('UPDATE sessions SET status="working"')
        recovery.import_workspace(self.source, self.target, 'old')
        with contextlib.closing(store.connect(self.target)) as c:
            self.assertEqual(tuple(c.execute('SELECT status,manual_pause FROM goals WHERE id="goal-leader"').fetchone()), ('paused', 1))
            self.assertEqual(c.execute('SELECT status FROM goal_executions WHERE id="execution-leader"').fetchone()[0], 'paused')
            self.assertEqual(c.execute('SELECT status FROM sessions WHERE session_id="leader"').fetchone()[0], 'offline')
            self.assertEqual(c.execute('SELECT status FROM goals WHERE id="goal-mib-leader"').fetchone()[0], 'completed')


if __name__ == '__main__':
    unittest.main()
