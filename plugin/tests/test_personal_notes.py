"""Quick ideas are ordinary local records until the user starts a discussion."""
import tests
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from crew import board, chat, personal, settings, store
from crew.web_api import personal as api
from test_chat import FakeProcess
from test_codex_runtime import FakeClient


class PersonalNotesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='crew-ideas-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'home'

    def capture(self, **fields):
        return api.save_note(self.home, {'action': 'capture', 'request_id': 'capture-1', 'text': '健身打卡 app',
                                         'tags': ['健身'], **fields})

    def project(self):
        root = self.root / 'fitness'
        root.mkdir()
        with closing(store.connect(self.home)) as conn:
            board.init_project(conn, 'fitness', root)
        return root

    def test_quick_capture_list_edit_and_archive_never_create_assistant_or_model(self):
        with (mock.patch('crew.settings.resolve', side_effect=AssertionError('no AI configuration')),
              mock.patch('subprocess.Popen', side_effect=AssertionError('no native process'))):
            self.assertEqual(api.notes(self.home), {'notes': [], 'model_calls': 0})
            self.assertFalse(self.home.exists())
            note = self.capture()['note']
            self.assertEqual(note['source'], {'type': 'human', 'surface': 'personal-notes'})
            self.assertEqual(len(api.notes(self.home)['notes']), 1)
            changed = api.save_note(self.home, {'action': 'update', 'request_id': 'edit-1', 'note_id': note['id'],
                'expected_version': note['version'], 'text': '简单健身打卡 app', 'state': 'archived'})['note']
            self.assertEqual(changed['original_text'], '健身打卡 app')
            self.assertEqual(changed['tags'], ['健身'])
            self.assertEqual(changed['source'], note['source'])
            self.assertEqual(api.notes(self.home)['notes'], [])
            self.assertEqual(api.notes(self.home, include_archived=True)['notes'][0]['text'], '简单健身打卡 app')
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)
            self.assertIsNone(conn.execute('SELECT * FROM personal_assistant').fetchone())

    def test_capture_and_update_retries_return_one_record_without_version_increments(self):
        first, again = self.capture(), self.capture()
        self.assertEqual(first['note']['id'], again['note']['id'])
        self.assertTrue(again['duplicate'])
        data = {'action': 'update', 'request_id': 'edit', 'note_id': first['note']['id'],
                'expected_version': 1, 'text': '修改内容', 'tags': ['应用']}
        updated = api.save_note(self.home, data)
        retried = api.save_note(self.home, data)
        self.assertTrue(retried['duplicate'])
        self.assertEqual(retried['note']['version'], updated['note']['version'])
        with self.assertRaisesRegex(board.Conflict, '不同的想法操作'):
            api.save_note(self.home, {**data, 'text': '另一个修改'})
        with self.assertRaisesRegex(board.Conflict, '已变化'):
            api.save_note(self.home, {**data, 'request_id': 'stale'})

    def test_explicit_start_uses_real_leader_flow_and_preserves_source_without_assistant(self):
        self.project()
        note = self.capture(project='fitness')['note']
        with closing(store.connect(self.home)) as conn:
            settings.save(conn, {'default': {'runtime': 'claude', 'model': None, 'effort': None}})
        manager = chat.Manager(self.home, Path(__file__).resolve().parents[1], lambda: [], process_factory=FakeProcess)
        self.addCleanup(manager.close)
        with mock.patch('crew.settings.shutil.which', return_value='fake-claude'), mock.patch('crew.chat.claude_executable', return_value='fake-claude'):
            first = manager.start_personal_note(note['id'], 'fitness', request_id='start-one')
        with mock.patch('crew.settings.resolve', side_effect=AssertionError('linked note needs no CLI')):
            duplicate = manager.start_personal_note(note['id'], 'fitness', request_id='new-retry-id')
        self.assertEqual(first['session_id'], duplicate['session_id'])
        self.assertTrue(duplicate['duplicate'])
        self.assertEqual(len(manager.processes), 1)
        saved = api.notes(self.home)['notes'][0]
        self.assertEqual(saved['leader']['source']['original_text'], '健身打卡 app')
        self.assertEqual(saved['leader']['source']['id'], note['id'])
        self.assertEqual(saved['leader']['status'], 'completed')
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 1)
            self.assertIsNone(conn.execute('SELECT * FROM personal_assistant').fetchone())
        message = next(item['text'] for item in manager.history(first['session_id'])['events'] if item['type'] == 'message')
        self.assertIn('尚未获得实施同意时先讨论', message)
        self.assertIn(note['id'], message)

    def test_unknown_start_and_concurrent_start_do_not_open_another_leader(self):
        self.project()
        note = self.capture()['note']
        calls = []
        def create(*args, **kwargs):
            calls.append(True)
            with closing(store.connect(self.home)) as second:
                with self.assertRaisesRegex(board.Conflict, 'pending'):
                    personal.start_note(second, note['id'], 'fitness', request_id='concurrent', create_leader=create)
            raise TimeoutError('创建结果尚未确认')
        with closing(store.connect(self.home)) as conn:
            with self.assertRaisesRegex(TimeoutError, '尚未确认'):
                personal.start_note(conn, note['id'], 'fitness', request_id='first', create_leader=create)
            with self.assertRaisesRegex(board.Conflict, 'unknown'):
                personal.start_note(conn, note['id'], 'fitness', request_id='another-key', create_leader=create)
        self.assertEqual(len(calls), 1)
        self.assertEqual(api.notes(self.home)['notes'][0]['leader']['error'], '创建结果尚未确认')

    def test_codex_starts_same_discussion_flow_and_configuration_failure_does_not_reserve_it(self):
        self.project()
        note = self.capture(project='fitness')['note']
        with closing(store.connect(self.home)) as conn:
            settings.save(conn, {'default': {'runtime': 'codex', 'model': None, 'effort': None}})
        manager = chat.Manager(self.home, Path(__file__).resolve().parents[1], lambda: [], process_factory=FakeProcess)
        self.addCleanup(manager.close)
        with mock.patch('crew.settings.resolve', side_effect=board.BoardError('尚未配置 Codex')):
            with self.assertRaisesRegex(board.BoardError, '尚未配置 Codex'):
                manager.start_personal_note(note['id'], 'fitness', request_id='start-codex')
        self.assertIsNone(api.notes(self.home)['notes'][0]['leader'])
        clients = []
        def client(**kwargs):
            clients.append(FakeClient(**kwargs))
            return clients[-1]
        with (mock.patch('crew.settings.shutil.which', return_value='fake-codex'),
              mock.patch('crew.codex_runtime._ObservedClient', side_effect=client)):
            result = manager.start_personal_note(note['id'], 'fitness', request_id='start-codex')
            repeated = manager.start_personal_note(note['id'], 'fitness', request_id='retry-codex')
        self.assertEqual(result['session_id'], repeated['session_id'])
        self.assertEqual(len(clients), 1)
        self.assertTrue(any(method == 'turn/start' for method, data in clients[0].calls))
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(board.session(conn, result['session_id'])['role'], 'leader')
            self.assertIsNone(conn.execute('SELECT * FROM personal_assistant').fetchone())
        clients[0].finish()

    def test_lost_creation_response_recovers_original_cc_and_codex_leader_without_replay(self):
        self.project()
        for runtime in ('claude', 'codex'):
            note = self.capture(project='fitness', request_id='note-' + runtime)['note']
            with closing(store.connect(self.home)) as conn:
                settings.save(conn, {'default': {'runtime': runtime, 'model': None, 'effort': None}})
            manager = chat.Manager(self.home, Path(__file__).resolve().parents[1], lambda: [], process_factory=FakeProcess)
            self.addCleanup(manager.close)
            original = manager._personal_leader
            def lost(*args, **kwargs):
                original(*args, **kwargs)
                raise TimeoutError('creation response lost')
            with (mock.patch('crew.settings.shutil.which', return_value='fake-carrier'),
                  mock.patch('crew.chat.claude_executable', return_value='fake-claude'),
                  mock.patch('crew.codex_runtime._ObservedClient', FakeClient),
                  mock.patch.object(manager, '_personal_leader', side_effect=lost)):
                recovered = manager.start_personal_note(note['id'], 'fitness', request_id='start-' + runtime)
            self.assertTrue(recovered['recovered'])
            sid = recovered['session_id']
            journal = manager.history(sid)['events']
            self.assertTrue(any(item['type'] == 'personal_note_origin' for item in journal))
            self.assertEqual(len([item for item in journal if item['type'] == 'message']), 1)
            self.assertIn('creation response lost', recovered['warning'])
            with mock.patch('crew.settings.resolve', side_effect=AssertionError('recovery must not launch')):
                again = manager.start_personal_note(note['id'], 'fitness', request_id='another-' + runtime)
            self.assertEqual(again['session_id'], sid)
            self.assertEqual(len(manager.processes), 1)
            self.assertEqual(recovered['note']['leader']['source']['recovery']['kind'], 'journal-receipt')
            if runtime == 'codex':
                manager.processes[sid].client.finish()

    def test_host_restart_finds_registration_journal_without_loading_model_configuration(self):
        self.project()
        note = self.capture(project='fitness')['note']
        with closing(store.connect(self.home)) as conn:
            settings.save(conn, {'default': {'runtime': 'claude', 'model': None, 'effort': None}})
        manager = chat.Manager(self.home, Path(__file__).resolve().parents[1], lambda: [], process_factory=FakeProcess)
        self.addCleanup(manager.close)
        with (mock.patch('crew.settings.shutil.which', return_value='fake-claude'),
              mock.patch.object(manager, '_launch', side_effect=SystemExit('simulated host exit'))):
            with self.assertRaises(SystemExit):
                manager.start_personal_note(note['id'], 'fitness', request_id='interrupted')
        self.assertEqual(api.notes(self.home)['notes'][0]['leader']['status'], 'pending')
        replacement = chat.Manager(self.home, Path(__file__).resolve().parents[1], lambda: [], process_factory=FakeProcess)
        self.addCleanup(replacement.close)
        with mock.patch('crew.settings.resolve', side_effect=AssertionError('no new launch')):
            result = replacement.start_personal_note(note['id'], 'fitness', request_id='recover')
        self.assertTrue(result['recovered']); self.assertEqual(replacement.processes, {})
        self.assertEqual(result['model_calls'], 0)

    def test_user_can_bind_unknown_to_verified_same_project_active_leader_without_sending(self):
        root = self.project()
        note = self.capture()['note']
        with closing(store.connect(self.home)) as conn:
            with self.assertRaises(TimeoutError):
                personal.start_note(conn, note['id'], 'fitness', request_id='unknown',
                    create_leader=mock.Mock(side_effect=TimeoutError('unknown result')))
            board.join(conn, 'existing', 'fitness', 'Original discussion', root, None, None, role='leader')
            board.join(conn, 'member', 'fitness', 'Member', root, None, None, group_name='Original discussion')
        data = {'note_id': note['id'], 'session_id': 'existing', 'request_id': 'manual-link', 'reason': '已核实原来的讨论'}
        with (mock.patch('crew.settings.resolve', side_effect=AssertionError('no model')),
              mock.patch('subprocess.Popen', side_effect=AssertionError('no launch'))):
            candidates = api.note_recovery(self.home, note['id'])
            self.assertEqual([leader['session_id'] for leader in candidates['leaders']], ['existing'])
            with self.assertRaises(board.BoardError):
                api.recover_note(self.home, {**data, 'session_id': 'member'})
            result = api.recover_note(self.home, data)
            again = api.recover_note(self.home, data)
        self.assertTrue(again['duplicate']); self.assertEqual(result['model_calls'], 0)
        self.assertEqual(result['note']['original_text'], note['original_text'])
        self.assertEqual(result['note']['leader']['source']['recovery']['previous_error'], 'unknown result')
        self.assertFalse((self.home / 'chat').exists())


if __name__ == '__main__':
    unittest.main()
