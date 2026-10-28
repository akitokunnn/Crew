"""Role preferences through real HTTP and injected Claude/Codex carriers."""
import threading
import unittest
from unittest import mock

import tests
import test_chat_http as helpers
from test_codex_runtime import FakeClient
from test_shell import NOW, PLUGIN, ShellFixture
from crew import board, models, runtimes, settings, store, web


class RolePreferenceHttpTests(ShellFixture, unittest.TestCase):
    close_server = helpers.ChatHttpTests.close_server
    request = helpers.ChatHttpTests.request

    def setUp(self):
        super().setUp()
        settings.save(self.conn, settings.DEFAULT)
        self.clients = []

        def client(**options):
            value = FakeClient(**options)
            value.thread_id = 'role-fixture-native-' + str(len(self.clients))
            self.clients.append(value)
            return value

        injected = mock.patch('crew.codex_runtime._ObservedClient', side_effect=client)
        injected.start()
        self.addCleanup(injected.stop)
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=mock.Mock(return_value=[]), claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory,
            isolated_sources=True)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = self.server.url

    @staticmethod
    def available(*names):
        return lambda command, *args, **kwargs: '/fixture/' + command if command in names else None

    def test_configuration_and_discussion_do_not_discover_cli_then_two_carriers_launch_role_preferences(self):
        with mock.patch('shutil.which', side_effect=AssertionError('Config/read/create must not inspect CLI')):
            code, leader, _ = self.request('/api/settings/effective?project=demo&purpose=leader')
            self.assertEqual(code, 200, leader)
            code, member, _ = self.request('/api/settings/effective?project=demo&purpose=member')
            self.assertEqual(code, 200, member)
            code, created, _ = self.request('/api/tasks/create', {'project': 'demo', 'text': 'Only retain discussion', 'request_id': 'role-discussion'})
            self.assertEqual(code, 200, created)
        self.assertEqual(leader['ai'], {'runtime': None, 'model': None, 'effort': None})
        self.assertEqual(leader['role_preference']['runtime'], 'claude')
        self.assertEqual(member['role_preference']['runtime'], 'codex')
        self.assertEqual(member['sources']['ai']['model']['scope'], 'builtin-role-preference')
        self.assertEqual(member['role_preference']['availability'], 'not_inspected')
        self.assertEqual(member['native_configuration']['loaded'], 'not_confirmed')
        self.assertEqual(self.processes, [])
        self.assertEqual(self.clients, [])
        code, chief, _ = self.request('/api/leader?project=demo', {'name': 'role-claude-chief', 'task': 'Finite leader fixture'})
        self.assertEqual(code, 200, chief)
        self.assertEqual(runtimes.get(self.conn, chief['session_id'])['runtime'], 'claude')
        self.assertEqual(chief['selection_resolution']['selected_runtime'], 'claude')
        self.assertNotIn('--model', self.processes[-1].command)
        self.assertNotIn('--effort', self.processes[-1].command)
        code, worker, _ = self.request('/api/spawn?project=demo', {'name': 'role-codex-worker', 'group': 'lead', 'task': 'Finite member fixture'})
        self.assertEqual(code, 200, worker)
        self.assertEqual(runtimes.get(self.conn, worker['session_id'])['runtime'], 'codex')
        self.assertEqual(models.get(self.conn, worker['session_id']), {'model': None, 'effort': None})
        self.assertFalse(worker['selection_resolution']['fallback'])
        self.assertEqual(len(self.clients), 1)
        params = next(value for method, value in self.clients[0].calls if method == 'thread/start')
        self.assertIsNone(params.get('model'))

    def test_missing_role_preference_uses_available_carrier_with_reason_but_explicit_missing_choice_fails(self):
        with mock.patch('shutil.which', side_effect=self.available('claude')):
            code, worker, _ = self.request('/api/spawn?project=demo', {'name': 'fallback-worker', 'group': 'lead', 'task': 'Finite fallback'})
            self.assertEqual(code, 200, worker)
            resolution = worker['selection_resolution']
            self.assertTrue(resolution['fallback'])
            self.assertEqual(resolution['available'], ['claude'])
            self.assertEqual(resolution['preferred_runtime'], 'codex')
            self.assertIn('未找到角色首选 codex', resolution['reason'])
            self.assertEqual(runtimes.get(self.conn, worker['session_id'])['runtime'], 'claude')
            code, _saved, _ = self.request('/api/member-defaults?project=demo', {'leader_session_id': 's3',
                'selection': {'runtime': 'codex', 'model': None, 'effort': None}})
            self.assertEqual(code, 200)
            count = len(self.processes)
            code, refused, _ = self.request('/api/spawn?project=demo', {'name': 'explicit-missing', 'group': 'lead', 'task': 'Cannot switch explicit carrier'})
            self.assertEqual(code, 400, refused)
            self.assertIn('已选择 codex', refused['error'])
            self.assertEqual(len(self.processes), count)
        with mock.patch('shutil.which', side_effect=self.available('codex')):
            code, chief, _ = self.request('/api/leader?project=demo', {'name': 'fallback-codex-chief', 'task': 'Finite fallback'})
            self.assertEqual(code, 200, chief)
            self.assertTrue(chief['selection_resolution']['fallback'])
            self.assertEqual(chief['selection_resolution']['selected_runtime'], 'codex')
            self.assertEqual(chief['selection_resolution']['loaded'], 'not_confirmed')

    def test_explicit_global_then_per_leader_override_do_not_reconfigure_existing_people_or_execution(self):
        code, old, _ = self.request('/api/leader?project=demo', {'name': 'existing-native-chief', 'task': 'Existing finite work'})
        self.assertEqual(code, 200, old)
        old_command, old_input = self.processes[0].command[:], self.processes[0].stdin.getvalue()
        before = runtimes.get(self.conn, 's1'), models.get(self.conn, 's1')
        code, _saved, _ = self.request('/api/settings', {'ai': {'default': {'runtime': 'codex', 'model': 'user-native-model', 'effort': 'future-native-effort'}}})
        self.assertEqual(code, 200)
        self.assertEqual(self.processes[0].command, old_command)
        self.assertEqual(self.processes[0].stdin.getvalue(), old_input)
        self.assertEqual(runtimes.get(self.conn, old['session_id'])['runtime'], 'claude')
        self.assertEqual((runtimes.get(self.conn, 's1'), models.get(self.conn, 's1')), before)
        code, worker, _ = self.request('/api/spawn?project=demo', {'name': 'global-codex-worker', 'group': 'lead', 'task': 'Global explicit future worker'})
        self.assertEqual(code, 200, worker)
        self.assertEqual(runtimes.get(self.conn, worker['session_id'])['runtime'], 'codex')
        self.assertEqual(models.get(self.conn, worker['session_id']), {'model': 'user-native-model', 'effort': 'future-native-effort'})
        self.assertNotIn('selection_resolution', worker)
        code, _saved, _ = self.request('/api/member-defaults?project=demo', {'leader_session_id': 's3',
            'selection': {'runtime': 'claude', 'model': None, 'effort': None}})
        self.assertEqual(code, 200)
        code, scoped, _ = self.request('/api/spawn?project=demo', {'name': 'explicit-leader-worker', 'group': 'lead', 'task': 'Per-leader explicit worker'})
        self.assertEqual(code, 200, scoped)
        self.assertEqual(runtimes.get(self.conn, scoped['session_id'])['runtime'], 'claude')
        self.assertEqual(models.get(self.conn, scoped['session_id']), {'model': None, 'effort': None})
        self.assertEqual(self.processes[0].stdin.getvalue(), old_input)

    def test_no_available_cli_returns_actual_error_without_registration_or_fake_process(self):
        before = len(board.sessions(self.conn, 'demo'))
        with mock.patch('shutil.which', return_value=None):
            code, error, _ = self.request('/api/leader?project=demo', {'name': 'no-carrier', 'task': 'No CLI fixture'})
        self.assertEqual(code, 400, error)
        self.assertIn('请先安装并登录', error['error'])
        self.assertEqual(len(board.sessions(self.conn, 'demo')), before)
        self.assertEqual(self.processes, [])
        self.assertEqual(self.clients, [])

    def test_project_roles_and_per_leader_override_reach_actual_fake_carriers(self):
        code, scoped, _ = self.request('/api/settings/scope', {'scope': 'project', 'identity': 'demo',
            'ai': {'leader': {'runtime': 'codex', 'model': None, 'effort': None},
                   'member': {'runtime': 'claude', 'model': 'project-member-model', 'effort': None}},
            'version': 0, 'request_id': 'role-project-scope'})
        self.assertEqual(code, 200, scoped)
        code, effective, _ = self.request('/api/settings/effective?project=demo&purpose=leader')
        self.assertEqual(code, 200, effective)
        self.assertIsNone(effective['role_preference'])
        self.assertEqual(effective['sources']['ai']['runtime']['scope'], 'project')
        code, chief, _ = self.request('/api/leader?project=demo', {'name': 'project-codex-chief', 'task': 'Project explicit leader'})
        self.assertEqual(code, 200, chief)
        self.assertEqual(runtimes.get(self.conn, chief['session_id'])['runtime'], 'codex')
        code, worker, _ = self.request('/api/spawn?project=demo', {'name': 'project-claude-worker', 'group': 'lead', 'task': 'Project explicit member'})
        self.assertEqual(code, 200, worker)
        self.assertEqual(runtimes.get(self.conn, worker['session_id'])['runtime'], 'claude')
        self.assertEqual(models.get(self.conn, worker['session_id'])['model'], 'project-member-model')
        code, _saved, _ = self.request('/api/member-defaults?project=demo', {'leader_session_id': 's3',
            'selection': {'runtime': 'codex', 'model': 'leader-specific-member-model', 'effort': None}})
        self.assertEqual(code, 200)
        code, selected, _ = self.request('/api/spawn?project=demo', {'name': 'leader-codex-worker', 'group': 'lead', 'task': 'Per-leader explicit member'})
        self.assertEqual(code, 200, selected)
        self.assertEqual(runtimes.get(self.conn, selected['session_id'])['runtime'], 'codex')
        self.assertEqual(models.get(self.conn, selected['session_id'])['model'], 'leader-specific-member-model')


if __name__ == '__main__':
    unittest.main()
