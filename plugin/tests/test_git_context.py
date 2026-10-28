"""A settings save is passive; changed Git duties arrive with ordinary input."""
import tests
import json
from types import SimpleNamespace
from unittest import mock

from crew import board, chat, git_admin, runtimes
from test_core import CoreCase, NOW
from test_chat import FakeProcess
from test_shell import PLUGIN


class GitContextTests(CoreCase):
    def setUp(self):
        super().setUp()
        board.join(self.conn, 's1', 'demo', 'chief', self.root, self.root, 'main',
                   role='leader', group_name='chief', now=NOW)
        self.conn.execute("INSERT INTO git_management(project,enabled_at,base,admin_session_id) VALUES('demo',?,'main','s1')", (NOW,))

    def test_changed_assignment_is_brief_and_identifies_current_owner_for_both_carriers(self):
        for runtime in ('claude', 'codex'):
            runtimes.set(self.conn, 's1', runtime)
            version, context = git_admin.input_context(self.conn, 's1', None, PLUGIN)
            self.assertIn('你现在负责', context)
            self.assertIn('skills', context)
            self.assertIn('crew_git' if runtime == 'codex' else 'crew git policy', context)
            self.assertLess(len(context), 650)
            self.assertEqual(git_admin.input_context(self.conn, 's1', version, PLUGIN), (version, None))
        _, worker_context = git_admin.input_context(self.conn, 's2', None, PLUGIN)
        self.assertIn('chief', worker_context)
        self.assertIn('你不是 Git 管理员', worker_context)

    def test_disabling_revokes_the_previous_assignment_once(self):
        previous, _ = git_admin.input_context(self.conn, 's1', None, PLUGIN)
        self.conn.execute("DELETE FROM git_management WHERE project='demo'")
        version, context = git_admin.input_context(self.conn, 's1', previous, PLUGIN)
        self.assertIsNone(version)
        self.assertIn('已停用', context)
        self.assertEqual(git_admin.input_context(self.conn, 's1', version, PLUGIN), (None, None))

    def test_owned_claude_input_applies_changes_without_polluting_visible_chat_or_repeating_rules(self):
        manager = chat.Manager(self.home, PLUGIN, lambda: [])
        process = FakeProcess([])
        journal = manager.journal('s1')
        current = chat.Running(process, journal)
        manager.processes['s1'] = current
        try:
            manager._send('s1', '继续完成工作')
            first = json.loads(process.stdin.getvalue().splitlines()[-1])['message']['content']
            self.assertIn('当前 Git 管理', first)
            self.assertIn('继续完成工作', first)
            self.assertEqual([e['text'] for e in journal.events if e['type'] == 'message'], ['继续完成工作'])
            manager._send('s1', '再继续')
            second = json.loads(process.stdin.getvalue().splitlines()[-1])['message']['content']
            self.assertEqual(second, '再继续')
            board.set_git_policy(self.conn, 'demo', 's1', 'Updated policy', now=NOW)
            manager._send('s1', '按新的规则')
            self.assertIn('当前 Git 管理', json.loads(process.stdin.getvalue().splitlines()[-1])['message']['content'])
        finally:
            manager.processes.clear(); process.finish(); manager.close()

    def test_owned_codex_input_retries_unaccepted_context_and_does_not_change_the_display_text(self):
        runtimes.set(self.conn, 's1', 'codex')
        manager = chat.Manager(self.home, PLUGIN, lambda: [])
        send = mock.Mock(side_effect=[ValueError('fixture rejected input'), 'accepted', 'again'])
        current = SimpleNamespace(runtime='codex', execution=None, send=send)
        manager.processes['s1'] = current
        try:
            with self.assertRaisesRegex(ValueError, 'fixture rejected input'):
                manager._send('s1', '继续工作')
            self.assertFalse(hasattr(current, 'git_context_revision'))
            manager._send('s1', '继续工作')
            self.assertIn('当前 Git 管理', send.call_args.args[0])
            self.assertEqual(send.call_args.kwargs['display_text'], '继续工作')
            manager._send('s1', '接着做')
            self.assertEqual(send.call_args.args[0], '接着做')
        finally:
            manager.processes.clear(); manager.close()
