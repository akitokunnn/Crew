"""Personality storage, permissions and settings through isolated entry points."""
import tests
from contextlib import closing
import json
import os
import sqlite3
import subprocess
import sys
import threading
import unittest
from unittest import mock
import zipfile

from crew import board, change_feed, persona, personal, session_ops, settings, store, web
from crew import workspace_assistant, workspace_settings
from crew.ops import executor, registry, tokens
from crew.ops.caller import Caller
from crew.ops.errors import OpError
from crew.storage_v2 import Repository
from crew.storage_v2 import legacy, packages
import test_chat_http
from test_shell import NOW, PLUGIN, ShellFixture
from tests.test_runtimes import RuntimeFixture


ACTOR = board.HUMAN
LIMIT_TEXT = 'a' * 60000
TOO_LONG = LIMIT_TEXT + 'a'


class EstimateTests(unittest.TestCase):
    def test_est_v2_uses_exact_rounding_and_counts_all_characters(self):
        for text, expected in (('', 0), ('abcdef', 2), ('中文', 3), ('ab中', 3),
                               ('\t\n ' * 1000, 1000), ('🙂🐈', 8), ('éΩ', 4),
                               ('if (x) {\n\treturn 1;\n}', 7), ('𠀀𰀀きカ한。　Ａ', 12),
                               (LIMIT_TEXT, 20000), (TOO_LONG, 20001)):
            with self.subTest(text=text[:30], length=len(text)):
                self.assertEqual(persona.estimate(text), expected)


class PersonaTests(RuntimeFixture):
    sid = 'chief-id'

    def assert_error(self, code, function, *args):
        with self.assertRaises(OpError) as error:
            function(self.conn, *args)
        self.assertEqual(error.exception.code, code)
        return error.exception

    def test_blank_and_three_independent_sessions(self):
        board.join(self.conn, 'second-id', 'demo', 'second', self.root, self.root, 'main', role='leader')
        helper = workspace_assistant.ensure(self.conn, self.home, 'demo')['session_id']
        people = (self.sid, 'second-id', helper)
        for session_id, subject in zip(people, ('leader', 'leader', 'workspace_assistant')):
            blank = persona.get(self.conn, session_id)
            self.assertEqual((blank['version'], blank['content'], blank['seed'], blank['growing']), (0, '', None, False))
            self.assertEqual(persona.subject(self.conn, session_id), subject)
            self.assertEqual(persona.save(self.conn, session_id, session_id, 0, ACTOR)['version'], 1)
        self.assertEqual({row['session_id'] for row in persona.list_personas(self.conn)['items']}, set(people))
        self.assertEqual([persona.get(self.conn, sid)['content'] for sid in people], list(people))
        self.assertEqual(persona.effective(self.conn, helper),
                         {'enabled': False, 'subject': 'workspace_assistant', 'content': helper, 'version': 1})
        self.assertIsNone(persona.effective(self.conn, 'member-id'))
        self.assert_error('persona_not_applicable', persona.get, 'member-id')
        self.assert_error('persona_not_applicable', persona.save, 'member-id', 'forbidden', 0, ACTOR)

    def test_presets_custom_clear_restore_and_freeze(self):
        sid = self.sid
        view = persona.reset(self.conn, sid, 'seed', 0, ACTOR)
        self.assertEqual(view['content'], '')
        for key in persona.PRESET_KEYS:
            view = persona.seed(self.conn, sid, key, 'discard me', view['version'], ACTOR)
            default = (PLUGIN / 'prompts' / 'personas' / (key + '.md')).read_text(encoding='utf-8')
            self.assertTrue(default.strip())
            self.assertEqual((view['seed'], view['seed_text'], view['content']), (key, None, default))
        self.assertEqual([item['key'] for item in view['presets']], list(persona.PRESET_KEYS))
        view = persona.seed(self.conn, sid, 'blank', None, view['version'], ACTOR)
        self.assertEqual((view['seed'], view['content']), ('blank', ''))
        for value in (None, '', '   ', '字' * 1001):
            self.assert_error('invalid_arguments', persona.seed, sid, 'custom', value, view['version'], ACTOR)
        self.assertEqual(persona.get(self.conn, sid), view)
        start = '轻松一点。' * 200
        custom = persona.seed(self.conn, sid, 'custom', start, view['version'], ACTOR)
        self.assertEqual((custom['seed_text'], custom['content']), (start, start))
        cleared = persona.reset(self.conn, sid, 'clear', custom['version'], ACTOR)
        self.assertEqual((cleared['seed'], cleared['content']), ('custom', ''))
        reset = persona.reset(self.conn, sid, 'seed', cleared['version'], ACTOR)
        self.assertEqual(reset['content'], start)
        restored = persona.restore(self.conn, sid, cleared['version'], reset['version'], ACTOR)
        self.assertEqual((restored['content'], restored['version']), ('', reset['version'] + 1))
        history = persona.history(self.conn, sid)
        self.assertEqual([row['action'] for row in history['history'][:4]], ['restore', 'reset', 'reset', 'seed'])
        self.assertTrue(all(row['actor'] == ACTOR for row in history['history']))
        frozen = persona.freeze(self.conn, sid, True)
        self.assertEqual(frozen, {**restored, 'frozen': True})
        self.assertEqual(persona.history(self.conn, sid), history)
        after = change_feed.latest_seq(self.conn)
        self.assertEqual(persona.freeze(self.conn, sid, True), frozen)
        self.assertEqual(change_feed.latest_seq(self.conn), after)
        with self.assertRaises(ValueError):
            persona.freeze(self.conn, sid, 1)

    def test_history_keeps_last_fifty_and_the_custom_seed_survives(self):
        sid = self.sid
        persona.seed(self.conn, sid, 'custom', '原始起点', 0, ACTOR)
        for version in range(1, 55):
            persona.save(self.conn, sid, '内容 %d' % version, version, ACTOR)
        first = persona.history(self.conn, sid, limit=20)
        second = persona.history(self.conn, sid, before=first['next_before'], limit=50)
        self.assertEqual([row['version'] for row in first['history'] + second['history']], list(range(55, 5, -1)))
        self.assertEqual(first['next_before'], 36)
        self.assertIsNone(second['next_before'])
        self.assert_error('not_found', persona.restore, sid, 1, 55, ACTOR)
        for limit in (0, 51, True):
            self.assert_error('invalid_arguments', persona.history, sid, None, limit)
        self.assertEqual(persona.reset(self.conn, sid, 'seed', 55, ACTOR)['content'], '原始起点')

    def test_every_content_write_rejects_overflow_and_conflicts_leave_the_version_intact(self):
        sid = self.sid
        valid = persona.seed(self.conn, sid, 'gentle', None, 0, ACTOR)
        valid = persona.save(self.conn, sid, LIMIT_TEXT, valid['version'], ACTOR)
        self.assertEqual((valid['percent'], valid['tokens_estimate']), (100, 20000))
        persona.set_enabled(self.conn, True)
        self.assert_error('conflict', persona.save, sid, 'stale', 1, ACTOR)
        # Simulate a longer bundled default and a historical entry from an earlier estimator.
        defaults = self.root / 'presets'
        defaults.mkdir()
        (defaults / 'gentle.md').write_text(TOO_LONG, encoding='utf-8')
        self.conn.execute('UPDATE persona_history SET content=? WHERE session_id=? AND version=1', (TOO_LONG, sid))
        history = persona.history(self.conn, sid)
        after = change_feed.latest_seq(self.conn)
        with mock.patch.object(persona, '_PRESET_ROOT', defaults):
            for function, args in (
                    (persona.save, (sid, TOO_LONG, 2, ACTOR)),
                    (persona.seed, (sid, 'gentle', None, 2, ACTOR)),
                    (persona.reset, (sid, 'seed', 2, ACTOR)),
                    (persona.restore, (sid, 1, 2, ACTOR)),
                    (persona.write_growth, (sid, TOO_LONG, '摘要', 2, 'cursor', NOW))):
                with self.subTest(write=function.__name__):
                    error = self.assert_error('persona_too_long', function, *args)
                    self.assertEqual(error.detail['tokens_estimate'], 20001)
                    self.assertIn('20001', str(error))
        view = persona.get(self.conn, sid)
        self.assertEqual((view['version'], view['content']), (2, LIMIT_TEXT))
        self.assertEqual(persona.history(self.conn, sid), history)
        self.assertEqual(change_feed.latest_seq(self.conn), after)

    def test_failed_history_insert_rolls_back_body_and_feed(self):
        sid = self.sid
        before = persona.save(self.conn, sid, '原文', 0, ACTOR)
        after = change_feed.latest_seq(self.conn)
        self.conn.execute("CREATE TEMP TRIGGER fail_history BEFORE INSERT ON persona_history "
                          "BEGIN SELECT RAISE(ABORT, 'history failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'history failure'):
            persona.save(self.conn, sid, '不能落库', 1, ACTOR)
        self.assertEqual(persona.get(self.conn, sid), before)
        self.assertEqual(change_feed.latest_seq(self.conn), after)
        self.conn.execute('DROP TRIGGER fail_history')
        self.assertEqual(persona.save(self.conn, sid, '再试', 1, ACTOR)['version'], 2)

    def test_growth_is_atomic_with_the_version_and_growth_metadata(self):
        sid = self.sid
        persona.save(self.conn, sid, '初稿', 0, ACTOR)
        args = (sid, '新正文', '更简洁了', 1, 'last-message', NOW)
        self.assertEqual(persona.write_growth(self.conn, *args), 'disabled')
        persona.set_enabled(self.conn, True)
        frozen = persona.freeze(self.conn, sid, True)
        self.assertEqual(persona.write_growth(self.conn, *args), 'frozen')
        self.assertEqual(persona.get(self.conn, sid), frozen)
        self.assertEqual(persona.write_growth(self.conn, 'member-id', *args[1:]), 'not_applicable')
        persona.freeze(self.conn, sid, False)
        self.conn.execute('UPDATE personas SET pending_turns=12 WHERE session_id=?', (sid,))
        persona.record_growth_error(self.conn, sid, '字' * 501)
        failure = persona.get(self.conn, sid)
        self.assertEqual((failure['version'], failure['pending_turns'], failure['growth_error']), (1, 12, '字' * 500))
        self.assertEqual(persona.write_growth(self.conn, *args), 'written')
        self.assertEqual(persona.write_growth(self.conn, *args), 'superseded')
        view = persona.get(self.conn, sid)
        self.assertEqual((view['content'], view['version'], view['pending_turns'], view['growth_error']), ('新正文', 2, 0, None))
        self.assertEqual((view['last_growth_at'], view['last_growth_summary']), (NOW, '更简洁了'))
        self.assertEqual(self.conn.execute('SELECT growth_cursor FROM personas WHERE session_id=?', (sid,)).fetchone()[0],
                         'last-message')
        row = persona.history(self.conn, sid)['history'][0]
        self.assertEqual((row['action'], row['actor'], row['summary']), ('grow', 'ai:growth', '更简洁了'))

    def test_switch_emits_only_session_metadata_when_value_changes(self):
        self.assertFalse(persona.enabled(self.conn))
        start = change_feed.latest_seq(self.conn)
        persona.set_enabled(self.conn, False)
        self.assertEqual(change_feed.latest_seq(self.conn), start)
        self.assertIsNone(self.conn.execute('SELECT enabled FROM persona_settings').fetchone())
        with self.assertRaises(ValueError):
            persona.set_enabled(self.conn, 1)
        persona.set_enabled(self.conn, True)
        end = change_feed.latest_seq(self.conn)
        persona.set_enabled(self.conn, True)
        self.assertEqual(change_feed.latest_seq(self.conn), end)
        changes = change_feed.read(self.conn, change_feed.scopes('demo'), start, change_feed.build_database_value)
        self.assertEqual([(row['type'], row['entity'], row['data']) for row in changes],
                         [('persona.updated', self.sid, {'session_id': self.sid})])

    def test_archive_and_workspace_removal_keep_rows_but_permanent_deletion_removes_them(self):
        persona.save(self.conn, self.sid, '要保留', 0, ACTOR)
        session_ops.archive(self.conn, self.sid)
        self.assertEqual(persona.list_personas(self.conn)['items'], [])
        session_ops.restore(self.conn, self.sid)
        workspace_settings.remove(self.conn, 'demo', workspace_settings.snapshot(self.conn, 'demo')['revision'])
        self.assertEqual(persona.list_personas(self.conn)['items'], [])
        self.assertEqual(self.conn.execute('SELECT content FROM personas').fetchone()[0], '要保留')
        board.init_project(self.conn, 'demo', self.root)
        self.assertEqual(persona.get(self.conn, self.sid)['content'], '要保留')
        session_ops.delete(self.conn, self.sid)
        self.assertIsNotNone(board.session(self.conn, self.sid))
        self.assertEqual(persona.list_personas(self.conn)['items'], [])
        for table in ('personas', 'persona_history'):
            self.assertEqual(self.conn.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)

    def test_workspace_package_contains_neither_persona_tables_nor_text(self):
        current, old = '本机正文 unique-current-persona', '历史正文 unique-history-persona'
        persona.set_enabled(self.conn, True)
        persona.seed(self.conn, self.sid, 'custom', old, 0, ACTOR)
        persona.save(self.conn, self.sid, current, 1, ACTOR)
        repository = Repository(self.root / 'repository')
        workspace_id = legacy.migrate_workspace(repository, self.home, 'demo')['workspace_id']
        package = self.root / 'workspace.crewpack'
        with closing(repository.connect()) as conn:
            packages.export_workspace(conn, repository.objects, workspace_id, package)
        with zipfile.ZipFile(package) as archive:
            for name in archive.namelist():
                data = archive.read(name)
                for value in (current, old):
                    self.assertNotIn(value.encode('utf-8'), data, name)
            database = self.root / 'package-data.sqlite'
            database.write_bytes(archive.read('data.sqlite'))
        with closing(sqlite3.connect(database)) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue(tables.isdisjoint(('personas', 'persona_history', 'persona_settings')))
            dump = '\n'.join(conn.iterdump())
            self.assertNotIn(current, dump)
            self.assertNotIn(old, dump)

    def test_desktop_settings_get_and_save_use_the_same_persisted_value(self):
        env = {**os.environ, 'PATH': str(self.root)}

        def worker(action, value=None):
            cmd = [sys.executable, '-m', 'crew.operations_chat', '--home', str(self.home),
                   '--backend', str(PLUGIN), '--action', action]
            return subprocess.run(cmd, cwd=PLUGIN, input=json.dumps(value), capture_output=True,
                                  text=True, encoding='utf-8', timeout=30, env=env)

        # Simulate settings opened before the backend has migrated an older database.
        self.conn.execute('DROP TABLE persona_settings')
        self.assertFalse(json.loads(worker('settings-get').stdout)['persona_enabled'])
        for value in (True, False):
            saved = worker('settings-save', {'persona_enabled': value})
            self.assertEqual(saved.returncode, 0, saved.stderr)
            reread = worker('settings-get')
            self.assertEqual(reread.returncode, 0, reread.stderr)
            self.assertIs(json.loads(saved.stdout)['persona_enabled'], value)
            self.assertIs(json.loads(reread.stdout)['persona_enabled'], value)
            self.assertIs(persona.enabled(self.conn), value)
        invalid = worker('settings-save', {'persona_enabled': 1})
        self.assertEqual(invalid.returncode, 1)
        self.assertIn('必须为 true 或 false', invalid.stderr)


class PersonaHttpTests(ShellFixture, unittest.TestCase):
    request = test_chat_http.ChatHttpTests.request
    close_server = test_chat_http.ChatHttpTests.close_server

    def setUp(self):
        super().setUp()
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project, agents_provider=lambda: [],
                                        claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
                                        now=lambda: store.parse_iso(NOW), process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = 'http://127.0.0.1:%d' % self.server.server_port

    def op(self, name, args, status=200, error=None, **options):
        code, value, _ = self.request('/api/op/' + name, args, **options)
        self.assertEqual(code, status, value)
        if error:
            self.assertEqual(value['code'], error)
        return value

    def test_save_get_schema_errors_and_request_replay(self):
        args = {'session_id': 's3', 'content': '先给结论。', 'expected_version': 0}
        saved = self.op('persona.save', args, request_id='save-once')
        self.assertEqual(saved, self.op('persona.get', {'session_id': 's3'}))
        self.assertEqual(saved, self.op('persona.get?project=demo&session_id=s3', None))
        self.assertEqual(self.op('persona.save', args, request_id='save-once'), {**saved, 'replayed': True})
        self.op('persona.save', args, 409, 'conflict')
        self.op('persona.save', {**args, 'expected_version': 1, 'content': TOO_LONG}, 400, 'persona_too_long')
        boundary = self.op('persona.save', {**args, 'expected_version': 1, 'content': LIMIT_TEXT})
        self.assertEqual(boundary['tokens_estimate'], 20000)
        self.op('persona.save', {**args, 'actor': 'somebody'}, 400, 'invalid_arguments')
        history = self.op('persona.history', {'session_id': 's3'})['history']
        self.assertEqual([(row['version'], row['actor']) for row in history], [(2, ACTOR), (1, ACTOR)])
        page = self.op('persona.history?session_id=s3&before=2&limit=1', None)
        self.assertEqual(page, {'history': history[1:], 'next_before': None})
        self.op('persona.history?session_id=s3&limit=no', None, 400, 'invalid_arguments')
        self.op('persona.get?session_id=s3&extra=1', None, 400, 'invalid_arguments')

    def test_subject_and_workspace_visibility_are_checked_on_every_operation(self):
        admin = personal.ensure_assistant(self.conn, self.home)['session_id']
        helper = workspace_assistant.ensure(self.conn, self.home, 'demo')['session_id']
        board.join(self.conn, 'git-id', 'demo', 'git', self.project, self.project, 'main', role='leader')
        self.conn.execute('INSERT INTO git_management(project,enabled_at,base,admin_session_id) VALUES(?,?,?,?)',
                          ('demo', NOW, 'main', 'git-id'))
        self.assertEqual(self.op('persona.get', {'session_id': helper})['subject'], 'workspace_assistant')
        self.assertEqual(self.op('persona.get', {'session_id': 's3'})['subject'], 'leader')
        operations = {'get': {}, 'save': {'content': 'x', 'expected_version': 0},
                      'seed': {'seed': 'blank', 'expected_version': 0},
                      'reset': {'mode': 'clear', 'expected_version': 0}, 'freeze': {'frozen': True},
                      'restore': {'version': 1, 'expected_version': 0}, 'history': {}, 'grow': {}}
        for suffix, args in operations.items():
            for sid in ('s1', 'git-id', admin):
                with self.subTest(op=suffix, sid=sid):
                    self.op('persona.' + suffix, {'session_id': sid, **args}, 400, 'persona_not_applicable')
        self.op('persona.get', {'session_id': 'missing'}, 404, 'not_found')
        self.op('persona.get?project=other', {'session_id': 's3'}, 404, 'not_visible')
        items = self.op('persona.list', {'project': 'demo'})['items']
        self.assertEqual({item['session_id'] for item in items}, {'s3', helper})
        session_ops.archive(self.conn, 's3')
        self.op('persona.get', {'session_id': 's3'}, 404, 'not_visible')
        other = self.root / 'other'
        other.mkdir()
        board.init_project(self.conn, 'other', other)
        other_helper = workspace_assistant.ensure(self.conn, self.home, 'other')['session_id']
        workspace_settings.remove(self.conn, 'other', workspace_settings.snapshot(self.conn, 'other')['revision'])
        self.op('persona.get', {'session_id': other_helper}, 404, 'not_visible')
        self.assertEqual([item['session_id'] for item in self.op('persona.list', {})['items']], [helper])

    def test_settings_and_ai_scope(self):
        self.assertFalse(self.op('settings.get', {})['persona_enabled'])
        self.assertTrue(self.op('settings.save', {'persona_enabled': True})['persona_enabled'])
        self.assertTrue(self.op('settings.get', {})['persona_enabled'])
        self.assertTrue(persona.enabled(self.conn))
        self.op('settings.save', {'persona_enabled': 0}, 400, 'invalid_arguments')
        service = self.server.service
        admin = Caller('assistant', session_id=personal.ensure_assistant(self.conn, self.home)['session_id'])
        grants = tokens.default_scopes(admin.role)
        self.assertEqual(set(registry.get('settings.save').schema_for(admin)['properties']), {'ai'})
        assistant_settings = executor.call(service, admin, 'settings.get', {}, grants=grants)
        self.assertIn('ai', assistant_settings)
        self.assertNotIn('persona_enabled', assistant_settings)
        with self.assertRaisesRegex(OpError, 'persona_enabled'):
            executor.call(service, admin, 'settings.save',
                          {'ai': settings.get(self.conn), 'persona_enabled': False}, grants=grants)
        operations = {name: op for name, op in registry.operations().items() if name.startswith('persona.')}
        self.assertEqual(set(operations), {'persona.' + action for action in
                                           ('get', 'save', 'seed', 'reset', 'freeze', 'restore', 'history', 'list', 'grow')})
        for op in operations.values():
            self.assertEqual(op.scopes, {'human', 'device_desktop', 'device_browser'})
            self.assertFalse(op.params['additionalProperties'])
        for caller in (admin, Caller('leader', session_id='s3', project='demo')):
            for operation in ('persona.get', 'persona.grow'):
                with self.assertRaises(OpError) as error:
                    executor.call(service, caller, operation, {'session_id': 's3'},
                                  grants=tokens.default_scopes(caller.role))
                self.assertEqual(error.exception.code, 'forbidden')
        self.assertFalse(self.op('settings.save', {'persona_enabled': False})['persona_enabled'])
        self.assertFalse(settings.persona_enabled(self.home))
