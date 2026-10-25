"""Real temporary HOME copies, SQLite backups and kernel exclusion; no service/AI."""
import tests
from contextlib import closing
import io
import json
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

from crew import board, goals, home_migration, install, operations_chat, store, supervisor
from crew.codex_runtime import process_identity
from crew.service_lock import HomeServiceLock
from crew.supervisor import _atomic_json


class HomeMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='crew-home-copy-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "原 HOME's data"
        self.target = self.root / "new parent's directory" / '新 HOME'
        self.source.mkdir()
        self.value = {'source': str(self.source), 'target': str(self.target)}
        self.internal = self.source / 'projects' / 'demo'
        self.external = self.root / 'business project'
        self.internal.mkdir(parents=True)
        (self.source / 'empty workspace' / 'empty child').mkdir(parents=True)
        self.external.mkdir()
        with closing(store.connect(self.source)) as conn:
            for name, root in (('inside', self.internal), ('outside', self.external)):
                conn.execute('INSERT INTO projects(name,root,created_at) VALUES(?,?,?)', (name, str(root), 'fixture'))
                conn.execute('INSERT INTO sessions(session_id,project,crew_name,cwd,worktree,started_at) VALUES(?,?,?,?,?,?)',
                             (name, name, name, str(root), str(root), 'fixture'))
            conn.execute('CREATE TABLE fixture_legacy(id INTEGER PRIMARY KEY, body TEXT)')
            conn.execute('INSERT INTO fixture_legacy VALUES(1,?)', ('keep ' + str(self.source),))
            conn.execute("INSERT INTO goals(id,project,leader_session_id,title,original_request,status,"
                         "requirement_version,current_round,created_at,updated_at) "
                         "VALUES('goal','inside','inside','迁移后仍保留的目标',?,'paused',1,1,'fixture','fixture')",
                         ('原始要求 ' + str(self.source),))
            conn.execute('INSERT INTO project_directory_changes(project,revision,old_root,new_root,session_paths_json,created_at) '
                         "VALUES('inside',1,?,?,?,'fixture')", (str(self.source / 'projects' / 'old'), str(self.internal),
                         json.dumps([{'session_id': 'inside', 'old_cwd': str(self.source / 'projects' / 'old'),
                                      'new_cwd': str(self.internal), 'old_worktree': None, 'new_worktree': str(self.internal)}])))
            document = self.internal / 'result.md'
            document.write_text('deliverable\n', encoding='utf-8')
            conn.execute('INSERT INTO documents(project,session_id,submitter,path,path_key,title,kind,source,created_at,updated_at) '
                         "VALUES('inside','inside','inside',?,?,'report','report','manual','fixture','fixture')",
                         (str(document), store.norm_path(document)))
        self.plugin = self.source / 'desktop' / 'backend' / 'fixture-code'
        self.plugin.mkdir(parents=True)
        (self.plugin / 'source.py').write_text('fixture code\n', encoding='utf-8')
        _atomic_json(self.source / 'runtime.json', {'plugin_root': str(self.plugin), 'python': 'fixture-python'})
        install._launchers(self.source, only_missing=False)
        self.chat = self.source / 'chat' / 'inside.jsonl'
        self.chat.parent.mkdir()
        self.chat.write_text(json.dumps({'type': 'message', 'id': 'm1', 'role': 'user',
                                         'text': '正文引用旧目录不该改：' + str(self.source), 'at': 'fixture'}) + '\n', encoding='utf-8')
        attachment = self.source / 'chat' / 'files' / 'inside' / 'image.png'
        attachment.parent.mkdir(parents=True)
        attachment.write_bytes(b'\x89PNG\0fixture attachment')
        _atomic_json(self.source / 'spawn' / 'one.json', {'md': str(self.source / 'spawn' / 'one.md'),
                                                       'cwd': str(self.external), 'task': str(self.source)})
        (self.source / 'spawn' / 'one.md').write_text('brief ' + str(self.source), encoding='utf-8')

    def database(self, home):
        return sqlite3.connect((home / 'crew.db').as_uri() + '?mode=ro&immutable=1', uri=True)

    def stable_bytes(self, root):
        return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*')
                if path.is_file() and not path.name.endswith('.lock')}

    def offline_work(self):
        with closing(store.connect(self.source)) as conn:
            board.join(conn, 'inside', 'inside', 'inside', self.internal, self.internal, None, role='leader')
            conn.execute("INSERT INTO goal_requirements VALUES('goal',1,?,'[]','[]',?,'user','{}','fixture')",
                         ('原始要求 ' + str(self.source), '原始要求 ' + str(self.source)))
            conn.execute("INSERT INTO goal_rounds VALUES('goal',1,1,'paused',NULL,NULL,'fixture',NULL)")
            conn.execute("UPDATE goals SET manual_pause=1,reason='existing manual pause' WHERE id='goal'")
            queued = goals.create(conn, 'inside', 'inside', 'queued offline', 'Keep queued work', ['keep it'])
            running = goals.create(conn, 'inside', 'inside', 'running offline', 'Keep interrupted work', ['keep it'])
            goals.transition(conn, running['id'], 'running')
            execution = goals.create_execution(conn, running['id'], 'inside', runtime='codex')
            goals.set_execution_status(conn, execution['id'], 'running')
            terminal = goals.create(conn, 'inside', 'inside', 'ended work', 'Keep ended history', ['keep it'])
            goals.transition(conn, terminal['id'], 'cancelled')
            conn.execute("INSERT INTO sessions(session_id,project,crew_name,status,started_at) VALUES('closed','inside','closed','closed','fixture')")
            conn.execute("INSERT INTO sessions(session_id,project,status,started_at) VALUES('unadopted','inside','offline','fixture')")
            conn.execute("INSERT INTO leader_notices(project,session_id,source,text,created_at) VALUES('inside','inside','crew','pending notice retained','fixture')")
            conn.execute("INSERT INTO session_stop_gates VALUES('outside','old-stop-time','old-stopper')")
            terminal_row = dict(conn.execute('SELECT * FROM goals WHERE id=?', (terminal['id'],)).fetchone())
        return queued['id'], running['id'], execution['id'], terminal_row

    def test_inspection_is_read_only_and_rejects_overlapping_or_relative_locations(self):
        before = self.stable_bytes(self.source)
        plan = home_migration.inspect(self.value)
        self.assertTrue(plan['can_copy'])
        self.assertEqual(plan['target_kind'], 'missing')
        self.assertFalse(self.target.exists())
        self.assertEqual(self.stable_bytes(self.source), before)
        for target in (self.source, self.source / 'child', self.source.parent, Path('relative')):
            with self.subTest(target=target):
                with self.assertRaises(home_migration.HomeMigrationError):
                    home_migration.inspect({**self.value, 'target': str(target)})

    def test_copy_preserves_database_schema_rows_attachments_and_relocates_only_owned_paths(self):
        before = self.stable_bytes(self.source)
        with closing(self.database(self.source)) as conn:
            schema = conn.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name').fetchall()
            counts = {row[0]: conn.execute('SELECT COUNT(*) FROM "' + row[0] + '"').fetchone()[0]
                      for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        result = home_migration.copy(self.value)
        self.assertTrue(result['copied'])
        self.assertTrue(result['source_retained'])
        self.assertTrue(result['can_connect'])
        self.assertTrue((self.target / 'empty workspace' / 'empty child').is_dir())
        self.assertEqual(self.stable_bytes(self.source), before)
        self.assertEqual((self.target / 'chat' / 'inside.jsonl').read_bytes(), self.chat.read_bytes())
        self.assertEqual((self.target / 'chat' / 'files' / 'inside' / 'image.png').read_bytes(), b'\x89PNG\0fixture attachment')
        with closing(self.database(self.target)) as conn:
            self.assertEqual(conn.execute('SELECT type,name,sql FROM sqlite_master ORDER BY name').fetchall(), schema)
            for table, count in counts.items():
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0], count, table)
            self.assertEqual(conn.execute("SELECT root FROM projects WHERE name='inside'").fetchone()[0], str(self.target / 'projects' / 'demo'))
            self.assertEqual(conn.execute("SELECT cwd FROM sessions WHERE session_id='outside'").fetchone()[0], str(self.external))
            self.assertEqual(conn.execute('SELECT body FROM fixture_legacy').fetchone()[0], 'keep ' + str(self.source))
            self.assertEqual(conn.execute('SELECT original_request FROM goals').fetchone()[0], '原始要求 ' + str(self.source))
            moved = json.loads(conn.execute('SELECT session_paths_json FROM project_directory_changes').fetchone()[0])[0]
            self.assertEqual(moved['new_cwd'], str(self.target / 'projects' / 'demo'))
            self.assertEqual(moved['old_cwd'], str(self.target / 'projects' / 'old'))
            self.assertEqual(conn.execute('SELECT path_key FROM documents').fetchone()[0], store.norm_path(self.target / 'projects' / 'demo' / 'result.md'))
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        spec = json.loads((self.target / 'spawn' / 'one.json').read_text(encoding='utf-8'))
        self.assertEqual(spec['md'], str(self.target / 'spawn' / 'one.md'))
        self.assertEqual(spec['cwd'], str(self.external))
        self.assertEqual(spec['task'], str(self.source))
        self.assertEqual((self.target / 'spawn' / 'one.md').read_bytes(), (self.source / 'spawn' / 'one.md').read_bytes())
        self.assertEqual(json.loads((self.target / 'runtime.json').read_text(encoding='utf-8'))['plugin_root'],
                         str(self.target / 'desktop' / 'backend' / 'fixture-code'))
        launcher = (self.target / 'bin' / 'crew').read_text(encoding='utf-8')
        self.assertIn(shlex.quote((self.target / 'crew.py').as_posix()), launcher)
        self.assertNotIn('.crew-home-migration-', launcher)

    def test_runtime_registries_and_browser_profiles_are_not_copied_but_history_is_kept(self):
        for relative in ('web.json', 'desktop/admin/status.json', 'desktop/admin/control.json',
                         'desktop/ui/Local Storage/cache', 'desktop/profiles/p1/Cookies'):
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}', encoding='utf-8')
        lease = self.source / 'runtime-locks' / 'old.json'
        _atomic_json(lease, {'pid': os.getpid(), 'created': 'old-process'})
        report = self.source / 'desktop' / 'admin' / 'operations' / 'incidents' / 'one' / 'report.html'
        report.parent.mkdir(parents=True)
        report.write_text('<p>history</p>', encoding='utf-8')
        _atomic_json(report.parents[2] / 'state.json', {'active_worker': None, 'incidents': [
            {'id': 'one', 'status': 'failed', 'report': str(report), 'evidence': {'text': str(self.source)}}]})
        home_migration.copy(self.value)
        for relative in ('web.json', 'desktop/admin/status.json', 'desktop/admin/control.json',
                         'desktop/ui', 'desktop/profiles', 'runtime-locks'):
            self.assertFalse((self.target / relative).exists(), relative)
        history = json.loads((self.target / 'desktop' / 'admin' / 'operations' / 'state.json').read_text(encoding='utf-8'))
        self.assertEqual(history['incidents'][0]['report'], str(self.target / report.relative_to(self.source)))
        self.assertEqual(history['incidents'][0]['evidence']['text'], str(self.source))
        self.assertEqual((self.target / report.relative_to(self.source)).read_text(encoding='utf-8'), '<p>history</p>')

    def test_existing_nonempty_destination_is_never_overwritten_and_existing_crew_can_be_connected(self):
        self.target.mkdir(parents=True)
        sentinel = self.target / 'user-file.txt'
        sentinel.write_text('keep me', encoding='utf-8')
        self.assertFalse(home_migration.inspect(self.value)['can_copy'])
        with self.assertRaises(home_migration.HomeMigrationError):
            home_migration.copy(self.value)
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'keep me')
        with closing(store.connect(self.target)):
            pass
        plan = home_migration.inspect(self.value)
        self.assertTrue(plan['can_connect'])
        self.assertFalse(plan['can_copy'])

    def test_held_service_or_native_lease_lock_prevents_copy(self):
        for relative in ('desktop/service.lock', 'desktop/supervisor.lock', 'runtime-locks/codex-fixture.lock'):
            with self.subTest(relative=relative):
                lock = HomeServiceLock(self.source)
                lock.path = self.source / relative
                self.assertTrue(lock.acquire())
                try:
                    with self.assertRaisesRegex(home_migration.HomeMigrationError, '运行|占有'):
                        home_migration.copy(self.value)
                finally:
                    lock.release()

    def test_running_independent_operations_chat_prevents_copy(self):
        _atomic_json(operations_chat.directory(self.source) / 'state.json', {
            'busy': True, 'error': None, 'working_since': 'fixture', 'partial': '',
            'request_id': 'fixture', 'worker': {'pid': os.getpid(), 'created': process_identity(os.getpid())}})
        with self.assertRaisesRegex(home_migration.HomeMigrationError, '运维 AI'):
            home_migration.copy(self.value)
        self.assertFalse((self.target / 'crew.db').exists())

    def test_source_and_target_service_and_chat_locks_are_held_for_the_copy(self):
        copy2 = home_migration.shutil.copy2
        checked = []
        def probe(old, new):
            if not checked:
                for home in (self.source, self.target):
                    for relative in ('desktop/service.lock', 'desktop/supervisor.lock',
                                     'desktop/admin/operations/chat/state.lock'):
                        lock = HomeServiceLock(home)
                        lock.path = home / relative
                        acquired = lock.acquire()
                        if acquired:
                            lock.release()
                        self.assertFalse(acquired, str(lock.path))
                checked.append(True)
            return copy2(old, new)
        with mock.patch.object(home_migration.shutil, 'copy2', side_effect=probe):
            home_migration.copy(self.value)
        self.assertTrue(checked)

    def test_reset_deployment_preserves_old_record_and_restores_current_runtime_source(self):
        deployment = self.source / 'desktop' / 'admin' / 'deployment.json'
        original = {'source': str(self.source / 'desktop' / 'backend' / 'retired-flat-runtime'),
                    'staged': str(self.root / 'old-git-release'), 'user_note': 'retain this exact record'}
        _atomic_json(deployment, original)
        before = deployment.read_bytes()
        self.assertEqual(supervisor.Supervisor(self.source)._deployment['source'], original['source'])
        result = home_migration.reset_deployment({'home': str(self.source)})
        self.assertTrue(result['changed'])
        self.assertFalse(deployment.exists())
        self.assertEqual(Path(result['archived']).read_bytes(), before)
        self.assertEqual(Path(result['archived']).parent, deployment.parent / 'deployment-history')
        current = str(Path(supervisor.__file__).resolve().parents[2])
        self.assertEqual(supervisor.Supervisor(self.source)._deployment, {'source': current, 'staged': None})
        again = home_migration.reset_deployment({'home': str(self.source)})
        self.assertFalse(again['changed'])
        self.assertIsNone(again['archived'])
        self.assertEqual(len(list((deployment.parent / 'deployment-history').glob('*.json'))), 1)

    def test_reset_deployment_without_old_record_is_a_noop_for_a_new_home(self):
        empty = self.root / 'empty new HOME'
        result = home_migration.reset_deployment({'home': str(empty)})
        self.assertFalse(result['changed'])
        self.assertFalse((empty / 'desktop' / 'admin' / 'deployment-history').exists())

    def test_reset_deployment_refuses_a_live_service_and_keeps_its_record(self):
        deployment = self.source / 'desktop' / 'admin' / 'deployment.json'
        _atomic_json(deployment, {'source': 'old-source', 'staged': None})
        before = deployment.read_bytes()
        for relative in ('desktop/service.lock', 'desktop/supervisor.lock', 'desktop/home-migration.lock'):
            with self.subTest(relative=relative):
                lock = HomeServiceLock(self.source)
                lock.path = self.source / relative
                self.assertTrue(lock.acquire())
                try:
                    with self.assertRaisesRegex(home_migration.HomeMigrationError, '后台仍在运行'):
                        home_migration.reset_deployment({'home': str(self.source)})
                finally:
                    lock.release()
                self.assertEqual(deployment.read_bytes(), before)

    def test_reset_deployment_cli_is_available_without_http_or_model(self):
        deployment = self.source / 'desktop' / 'admin' / 'deployment.json'
        _atomic_json(deployment, {'source': 'old-source', 'staged': None})
        with (mock.patch('sys.stdin', io.StringIO(json.dumps({'home': str(self.source)}))),
              mock.patch('sys.stdout', new_callable=io.StringIO) as output,
              mock.patch.object(operations_chat.subprocess, 'Popen', side_effect=AssertionError('no model'))):
            self.assertEqual(operations_chat.main(['--home', str(self.source), '--backend', str(self.plugin),
                                                  '--action', 'reset-deployment']), 0)
        self.assertTrue(json.loads(output.getvalue())['changed'])

    def test_prepare_offline_home_pauses_work_and_gates_notices_without_altering_terminal_history(self):
        queued, running, execution, terminal = self.offline_work()
        before_chat = self.chat.read_bytes()
        deployment = self.source / 'desktop' / 'admin' / 'deployment.json'
        _atomic_json(deployment, {'source': 'preserved-release', 'staged': None})
        before_deployment = deployment.read_bytes()
        with mock.patch.object(operations_chat.subprocess, 'Popen', side_effect=AssertionError('no native process')):
            result = home_migration.prepare_home({'home': str(self.source), 'reset_deployment': False})
        self.assertEqual(set(result['paused_goals']), {queued, running})
        self.assertEqual(result['gated_sessions'], ['inside'])
        self.assertIsNone(result['deployment'])
        self.assertEqual(deployment.read_bytes(), before_deployment)
        self.assertEqual(self.chat.read_bytes(), before_chat)
        with closing(store.connect(self.source)) as conn:
            for goal_id in (queued, running):
                goal = goals.get(conn, goal_id)
                self.assertEqual((goal['status'], goal['manual_pause']), ('paused', True))
                self.assertEqual(goal['reason'], '后台连接设置变更')
            self.assertEqual(goals.get_execution(conn, execution)['status'], 'paused')
            self.assertEqual(dict(conn.execute('SELECT * FROM goals WHERE id=?', (terminal['id'],)).fetchone()), terminal)
            self.assertEqual(conn.execute('SELECT sent_at FROM leader_notices').fetchone()[0], None)
            self.assertEqual(tuple(conn.execute("SELECT stopped_at,stopped_by FROM session_stop_gates WHERE session_id='outside'").fetchone()),
                             ('old-stop-time', 'old-stopper'))
            self.assertIsNone(conn.execute("SELECT 1 FROM session_stop_gates WHERE session_id IN ('closed','unadopted')").fetchone())
            before_goals = [tuple(row) for row in conn.execute('SELECT * FROM goals ORDER BY id')]
        again = home_migration.prepare_home({'home': str(self.source), 'reset_deployment': False})
        self.assertEqual(again['paused_goals'], [])
        self.assertEqual(again['gated_sessions'], [])
        with closing(store.connect(self.source)) as conn:
            self.assertEqual([tuple(row) for row in conn.execute('SELECT * FROM goals ORDER BY id')], before_goals)

    def test_prepare_home_can_initialize_empty_home_and_optionally_archive_same_lock_deployment(self):
        self.offline_work()
        deployment = self.source / 'desktop' / 'admin' / 'deployment.json'
        _atomic_json(deployment, {'source': 'old-runtime', 'staged': None})
        result = home_migration.prepare_home({'home': str(self.source), 'reset_deployment': True})
        self.assertTrue(result['deployment']['changed'])
        self.assertTrue(Path(result['deployment']['archived']).exists())
        empty = self.root / 'brand new HOME'
        result = home_migration.prepare_home({'home': str(empty), 'reset_deployment': False})
        self.assertEqual(result['paused_goals'], [])
        self.assertEqual(result['gated_sessions'], [])
        self.assertTrue(home_migration._is_home(empty))

    def test_prepare_home_refuses_live_service_before_changing_work(self):
        self.offline_work()
        lock = HomeServiceLock(self.source)
        self.assertTrue(lock.acquire())
        try:
            with self.assertRaisesRegex(home_migration.HomeMigrationError, '后台仍在运行'):
                home_migration.prepare_home({'home': str(self.source), 'reset_deployment': False})
        finally:
            lock.release()
        with closing(store.connect(self.source)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM goals WHERE status='running'").fetchone()[0], 1)

    def test_prepare_home_cli_initializes_without_http_or_model(self):
        empty = self.root / 'new CLI HOME'
        with (mock.patch('sys.stdin', io.StringIO(json.dumps({'home': str(empty), 'reset_deployment': False}))),
              mock.patch('sys.stdout', new_callable=io.StringIO) as output,
              mock.patch.object(operations_chat.subprocess, 'Popen', side_effect=AssertionError('no model'))):
            self.assertEqual(operations_chat.main(['--home', str(empty), '--backend', str(self.plugin),
                                                  '--action', 'prepare-home']), 0)
        self.assertTrue(json.loads(output.getvalue())['ok'])

    def test_copy_failure_keeps_original_data_and_retries_the_owned_staging_directory(self):
        before = self.stable_bytes(self.source)
        with mock.patch.object(home_migration.shutil, 'copy2', side_effect=OSError('fixture full disk')):
            with self.assertRaisesRegex(home_migration.HomeMigrationError, 'fixture full disk'):
                home_migration.copy(self.value)
        self.assertEqual(self.stable_bytes(self.source), before)
        self.assertFalse((self.target / 'crew.db').exists())
        self.assertTrue(home_migration.inspect(self.value)['resumable'])
        with self.chat.open('a', encoding='utf-8') as stream:
            stream.write('new source bytes before unpublished staging retry\n')
        result = home_migration.copy(self.value)
        self.assertTrue(result['copied'])
        self.assertEqual((self.target / 'chat' / 'inside.jsonl').read_bytes(), self.chat.read_bytes())
        self.assertTrue(home_migration.copy(self.value)['already_copied'])

    def test_inspect_copy_inspect_retry_reuses_completed_copy_but_rechecks_source_service_lock(self):
        first_plan = home_migration.inspect(self.value)
        self.assertTrue(first_plan['can_copy'])
        self.assertFalse(first_plan['already_copied'])
        home_migration.copy(self.value)
        retry_plan = home_migration.inspect(self.value)
        self.assertFalse(retry_plan['can_copy'])
        self.assertTrue(retry_plan['already_copied'])
        self.assertTrue(retry_plan['can_connect'])
        before = self.stable_bytes(self.target)
        lock = HomeServiceLock(self.source)
        self.assertTrue(lock.acquire())
        try:
            with self.assertRaisesRegex(home_migration.HomeMigrationError, '后台仍在运行'):
                home_migration.copy(self.value)
        finally:
            lock.release()
        result = home_migration.copy(self.value)
        self.assertTrue(result['already_copied'])
        self.assertEqual(self.stable_bytes(self.target), before)

    def test_completed_copy_is_not_reused_after_source_database_or_chat_changes(self):
        home_migration.copy(self.value)
        self.assertTrue(home_migration.inspect(self.value)['already_copied'])
        _atomic_json(self.source / 'desktop' / 'admin' / 'status.json', {'state': 'healthy', 'pid': 123})
        cache = self.source / 'desktop' / 'ui' / 'Cache' / 'fixture-cache'
        cache.parent.mkdir(parents=True)
        cache.write_text('irrelevant browser cache', encoding='utf-8')
        (self.source / 'desktop' / 'service.lock').touch()
        self.assertTrue(home_migration.inspect(self.value)['already_copied'])
        before = self.stable_bytes(self.target)
        with closing(sqlite3.connect(self.source / 'crew.db')) as conn:
            conn.execute("UPDATE fixture_legacy SET body='new work after returning to original HOME' WHERE id=1")
            conn.commit()
        plan = home_migration.inspect(self.value)
        self.assertFalse(plan['already_copied'])
        self.assertFalse(plan['can_copy'])
        self.assertTrue(plan['can_connect'])
        self.assertEqual(plan['reason'], home_migration.SOURCE_CHANGED)
        with self.assertRaisesRegex(home_migration.HomeMigrationError, '源数据已变化'):
            home_migration.copy(self.value)
        self.assertEqual(self.stable_bytes(self.target), before)
        another = self.root / 'another copy'
        value = {**self.value, 'target': str(another)}
        home_migration.copy(value)
        with self.chat.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'type': 'message', 'id': 'new', 'role': 'user', 'text': 'new discussion', 'at': 'fixture'}) + '\n')
        plan = home_migration.inspect(value)
        self.assertFalse(plan['already_copied'])
        self.assertFalse(plan['can_copy'])
        self.assertEqual(plan['reason'], home_migration.SOURCE_CHANGED)

    def test_completed_copy_checks_source_fingerprint_again_after_taking_locks(self):
        home_migration.copy(self.value)
        self.assertTrue(home_migration.inspect(self.value)['already_copied'])
        before = self.stable_bytes(self.target)
        idle = home_migration._idle
        def change_after_initial_inspection(home):
            idle(home)
            if home == self.source:
                with self.chat.open('a', encoding='utf-8') as stream:
                    stream.write('new bytes before source lock was acquired\n')
        with mock.patch.object(home_migration, '_idle', side_effect=change_after_initial_inspection):
            with self.assertRaisesRegex(home_migration.HomeMigrationError, '源数据已变化'):
                home_migration.copy(self.value)
        self.assertEqual(self.stable_bytes(self.target), before)

    def test_partial_publication_can_resume_without_overwriting_completed_files(self):
        replace = home_migration.os.replace
        once = [False]
        def fail_mid_publish(old, new):
            if Path(new) == self.target / 'crew.db' and not once[0]:
                once[0] = True
                raise OSError('fixture publication interrupted')
            return replace(old, new)
        with mock.patch.object(home_migration.os, 'replace', side_effect=fail_mid_publish):
            with self.assertRaisesRegex(home_migration.HomeMigrationError, 'publication interrupted'):
                home_migration.copy(self.value)
        self.assertTrue(home_migration.inspect(self.value)['resumable'])
        result = home_migration.copy(self.value)
        self.assertTrue(result['copied'])
        self.assertTrue((self.target / 'crew.db').is_file())

    def test_partial_publication_from_older_source_cannot_resume_after_source_changes(self):
        replace = home_migration.os.replace
        def fail_mid_publish(old, new):
            if Path(new) == self.target / 'crew.db':
                raise OSError('fixture publication interrupted')
            return replace(old, new)
        with mock.patch.object(home_migration.os, 'replace', side_effect=fail_mid_publish):
            with self.assertRaises(home_migration.HomeMigrationError):
                home_migration.copy(self.value)
        self.assertTrue(home_migration.inspect(self.value)['resumable'])
        before = self.stable_bytes(self.target)
        with self.chat.open('a', encoding='utf-8') as stream:
            stream.write('source used again after partial publication\n')
        plan = home_migration.inspect(self.value)
        self.assertFalse(plan['resumable'])
        self.assertFalse(plan['can_copy'])
        self.assertFalse(plan['already_copied'])
        self.assertEqual(plan['reason'], home_migration.SOURCE_CHANGED)
        with self.assertRaisesRegex(home_migration.HomeMigrationError, '源数据已变化'):
            home_migration.copy(self.value)
        self.assertEqual(self.stable_bytes(self.target), before)

    def test_sqlite_backup_includes_committed_wal_without_writing_source_sidecars(self):
        writer = sqlite3.connect(self.source / 'crew.db')
        self.addCleanup(writer.close)
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute("INSERT INTO fixture_legacy VALUES(2,'committed only in WAL')")
        writer.commit()
        self.assertTrue((self.source / 'crew.db-wal').is_file())
        before = self.stable_bytes(self.source)
        home_migration.copy(self.value)
        self.assertEqual(self.stable_bytes(self.source), before)
        with closing(self.database(self.target)) as conn:
            self.assertEqual(conn.execute('SELECT body FROM fixture_legacy WHERE id=2').fetchone()[0],
                             'committed only in WAL')
        self.assertTrue(home_migration.inspect(self.value)['already_copied'])
        writer.execute("INSERT INTO fixture_legacy VALUES(3,'source WAL changed after copying')")
        writer.commit()
        self.assertFalse(home_migration.inspect(self.value)['already_copied'])

    def test_retry_does_not_overwrite_foreign_files_added_to_partial_target(self):
        replace = home_migration.os.replace
        def fail_mid_publish(old, new):
            if Path(new) == self.target / 'crew.db':
                raise OSError('fixture publication interrupted')
            return replace(old, new)
        with mock.patch.object(home_migration.os, 'replace', side_effect=fail_mid_publish):
            with self.assertRaises(home_migration.HomeMigrationError):
                home_migration.copy(self.value)
        foreign = self.target / 'user-added.txt'
        foreign.write_text('user data stays', encoding='utf-8')
        with self.assertRaisesRegex(home_migration.HomeMigrationError, '出现已有数据'):
            home_migration.copy(self.value)
        self.assertEqual(foreign.read_text(encoding='utf-8'), 'user data stays')

    def test_source_link_is_rejected_without_following_it(self):
        link = self.source / 'recursive-link'
        try:
            link.symlink_to(self.source, target_is_directory=True)
        except OSError as error:
            self.skipTest('symlink unavailable: ' + str(error))
        with self.assertRaisesRegex(home_migration.HomeMigrationError, '符号链接|重解析'):
            home_migration.copy(self.value)

    @unittest.skipUnless(os.name == 'nt', 'Windows junction boundary')
    def test_windows_junction_target_is_rejected_before_copying(self):
        junction = self.root / 'junction'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(self.source)], capture_output=True)
        if result.returncode:
            self.skipTest('junction creation unavailable')
        self.addCleanup(lambda: os.rmdir(junction) if junction.exists() else None)
        with self.assertRaisesRegex(home_migration.HomeMigrationError, '重解析'):
            home_migration.inspect({**self.value, 'target': str(junction / 'nested')})

    @unittest.skipUnless(os.name == 'nt', 'Windows junction recursion')
    def test_windows_junction_inside_source_does_not_recurse_or_copy_outside_home(self):
        junction = self.source / 'junction-loop'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(self.source)], capture_output=True)
        if result.returncode:
            self.skipTest('junction creation unavailable')
        self.addCleanup(lambda: os.rmdir(junction) if junction.exists() else None)
        with self.assertRaisesRegex(home_migration.HomeMigrationError, '重解析'):
            home_migration.copy(self.value)
        self.assertFalse((self.target / 'crew.db').exists())

    def test_cli_actions_are_independent_of_http_and_do_not_start_a_native_turn(self):
        arguments = ['--home', str(self.source), '--backend', str(self.plugin)]
        for action in ('home-inspect', 'home-copy'):
            with self.subTest(action=action):
                with (mock.patch('sys.stdin', io.StringIO(json.dumps(self.value))),
                      mock.patch('sys.stdout', new_callable=io.StringIO) as output,
                      mock.patch.object(operations_chat.subprocess, 'Popen', side_effect=AssertionError('no model'))):
                    self.assertEqual(operations_chat.main([*arguments, '--action', action]), 0)
                result = json.loads(output.getvalue())
                self.assertTrue(result['can_copy'] if action == 'home-inspect' else result['copied'])


if __name__ == '__main__':
    unittest.main()
