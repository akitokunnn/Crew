"""Ordinary workspaces remain useful while Git features declare their requirements."""
import tests
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from crew import board, git_view, store, web
import test_chat_http as chat_helpers
from test_shell import NOW, PLUGIN, ShellFixture


class WorkspaceGitCapabilityTests(ShellFixture, unittest.TestCase):
    close_server = chat_helpers.ChatHttpTests.close_server
    request = chat_helpers.ChatHttpTests.request
    finish_turn = chat_helpers.ChatHttpTests.finish_turn

    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory(prefix='crew-ordinary-workspace-')
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name) / 'ordinary notes'
        self.workspace.mkdir()
        probe = subprocess.run(['git', '-C', str(self.workspace), 'rev-parse', '--show-toplevel'],
                               capture_output=True, text=True)
        self.assertNotEqual(probe.returncode, 0, 'The fixture must really be outside a Git worktree')
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.workspace,
            agents_provider=mock.Mock(return_value=[]), claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = self.server.url
        code, result, _ = self.request('/api/project/init', {'name': 'ordinary', 'root': str(self.workspace)})
        self.assertEqual(code, 200, result)

    def assert_unavailable(self, reason):
        code, result, _ = self.request('/api/git?project=ordinary')
        self.assertEqual(code, 200, result)
        self.assertIs(result['available'], False)
        self.assertIn(reason, result['reason'])
        self.assertEqual(result['choices'], {'branches': [], 'base': None, 'stable': None})
        self.assertEqual(result['repository'], self.workspace.name)
        self.assertIn('requests', result)
        self.assertIn('last_fetch_at', result)
        return result

    def assert_git_creation_denied(self, reason):
        sessions, groups = board.sessions(self.conn, 'ordinary'), board.groups(self.conn, 'ordinary')
        files = set((self.home / 'spawn').glob('*'))
        processes = len(self.processes)
        for route, body in (
            ('/api/git/enable', {}),
            ('/api/leader', {'name': 'isolated-chief', 'task': 'lead', 'new_worktree': 'feature/chief'}),
            ('/api/spawn', {'name': 'isolated-worker', 'task': 'work', 'new_worktree': 'feature/worker'}),
        ):
            with self.subTest(route=route):
                code, result, _ = self.request(route + '?project=ordinary', body)
                self.assertEqual(code, 400, result)
                self.assertIn(reason, result['error'])
        self.assertEqual(board.sessions(self.conn, 'ordinary'), sessions)
        self.assertEqual(board.groups(self.conn, 'ordinary'), groups)
        self.assertEqual(set((self.home / 'spawn').glob('*')), files)
        self.assertEqual(len(self.processes), processes)
        self.assertFalse((self.workspace / 'Temp').exists())

    def test_plain_http_workspace_leader_member_goal_chat_documents_and_git_page(self):
        code, leader, _ = self.request('/api/leader?project=ordinary', {'name': 'chief', 'task': 'Coordinate notes'})
        self.assertEqual(code, 200, leader)
        sid = leader['session_id']
        self.finish_turn(sid, self.processes[-1])
        code, member, _ = self.request('/api/spawn?project=ordinary', {'name': 'writer', 'task': 'Write notes', 'group': 'chief'})
        self.assertEqual(code, 200, member)
        self.finish_turn(member['session_id'], self.processes[-1])
        code, chat, _ = self.request('/api/chat/send?project=ordinary', {'session_id': sid, 'text': 'Review these notes'})
        self.assertEqual(code, 200, chat)
        self.finish_turn(sid, self.processes[0])
        code, created, _ = self.request('/api/goals/create?project=ordinary', {
            'leader_session_id': sid, 'title': 'Summarize notes', 'request': 'Write a summary', 'acceptance': ['A summary exists']})
        self.assertEqual(code, 200, created)
        goal = created['goal']
        code, sent, _ = self.request('/api/chat/send?project=ordinary', {'session_id': sid, 'goal_id': goal['id'], 'text': 'Begin summary'})
        self.assertEqual(code, 200, sent)
        self.finish_turn(sent['execution_id'], self.processes[-1])
        report = self.workspace / 'summary.md'
        report.write_text('# Summary\nUseful notes.', encoding='utf-8')
        code, document, _ = self.request('/api/documents?project=ordinary', {
            'session_id': sid, 'path': str(report), 'title': 'Summary', 'summary': 'Useful notes', 'goal_id': goal['id']})
        self.assertEqual(code, 200, document)
        code, detail, _ = self.request('/api/goal?project=ordinary&id=' + goal['id'])
        self.assertEqual(code, 200, detail)
        self.assertEqual([row['id'] for row in detail['documents']], [document['id']])
        result = self.assert_unavailable('Git 仓库')
        self.assertFalse(result['enabled'])
        self.assertIsNone(result['config'])
        self.assert_git_creation_denied('Git 仓库')
        self.assertFalse((self.workspace / '.git').exists())

    def test_missing_git_is_unavailable_without_losing_the_saved_configuration(self):
        subprocess.run(['git', '-C', str(self.workspace), 'init', '-b', 'main'], check=True, capture_output=True)
        self.conn.execute('INSERT INTO git_management(project,enabled_at,base,stable) VALUES(?,?,?,?)',
                          ('ordinary', NOW, 'main', None))
        self.conn.commit()
        real_which = shutil.which
        with mock.patch.object(git_view.shutil, 'which', side_effect=lambda exe, *a, **kw: None if exe == 'git' else real_which(exe, *a, **kw)):
            result = self.assert_unavailable('安装 Git')
            self.assertTrue(result['enabled'])
            self.assertEqual(result['config']['base'], 'main')
            self.assert_git_creation_denied('安装 Git')
            for route in ('/api/git/fetch?project=ordinary', '/api/git/commit?project=ordinary&sha=abcd'):
                code, denied, _ = self.request(route, {} if '/fetch' in route else None)
                self.assertEqual(code, 400, denied)
                self.assertIn('安装 Git', denied['error'])

    def test_damaged_git_marker_remains_an_error_instead_of_plain_workspace(self):
        (self.workspace / '.git').write_text('gitdir: nonexistent\n', encoding='ascii')
        code, result, _ = self.request('/api/git?project=ordinary')
        self.assertEqual(code, 400, result)
        self.assertIn('fatal:', result['error'])
        self.assertNotIn('available', result)


if __name__ == '__main__':
    unittest.main()
