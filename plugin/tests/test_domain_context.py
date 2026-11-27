"""Leaders read their workspace's architecture board, and a placed leader hears its own place with its inputs."""
import tests  # Install the production-service connection guard.
import json
from types import SimpleNamespace
from unittest import mock

from crew import board, chat, domains, runtimes
from crew.ops import registry, tokens
from crew.ops.errors import OpError
from test_core import CoreCase, NOW
from test_chat import FakeProcess
from test_shell import PLUGIN


class DomainContextTests(CoreCase):
    def setUp(self):
        super().setUp()
        for sid, name in (('voice', '语音组'), ('client', '客户端组')):
            board.join(self.conn, sid, 'demo', name, self.root, self.root, 'main', role='leader', group_name=name, now=NOW)
        board.init_project(self.conn, 'other', self.base, now=NOW)
        board.join(self.conn, 'far', 'other', '别处', self.base, self.base, 'main', role='leader', group_name='别处', now=NOW)
        domains.apply(self.conn, 'demo', [
            {'op': 'create', 'id': 'n-product', 'name': '产品', 'parent_id': None, 'x': 0, 'y': 0},
            {'op': 'create', 'id': 'n-client', 'name': '客户端', 'parent_id': 'n-product', 'x': 0, 'y': 0},
            {'op': 'create', 'id': 'n-voice', 'name': '语音', 'parent_id': 'n-client', 'x': 0, 'y': 0},
            {'op': 'create', 'id': 'n-screen', 'name': '界面', 'parent_id': 'n-client', 'x': 0, 'y': 0},
            {'op': 'assign', 'session_id': 'voice', 'domain_id': 'n-voice'},
        ], now=NOW)
        domains.apply(self.conn, 'other', [{'op': 'create', 'id': 'n-secret', 'name': '别处节点', 'parent_id': None, 'x': 0, 'y': 0}], now=NOW)

    def test_a_leader_reads_its_own_workspace_board_without_layout(self):
        board_view = self.agent('domains.get', sid='voice')
        self.assertEqual(board_view['project'], 'demo')
        self.assertEqual(sorted(part['name'] for part in board_view['parts']), sorted(['产品', '客户端', '语音', '界面']))
        self.assertIn({'id': 'n-voice', 'parent_id': 'n-client', 'name': '语音'}, board_view['parts'])
        self.assertEqual([(leader['name'], leader['domain_id'], leader['lead']) for leader in board_view['leaders']],
                         [('语音组', 'n-voice', False)])
        with self.assertRaises(OpError) as caught:
            self.agent('domains.get', {'project': 'other'}, sid='voice')
        self.assertEqual(caught.exception.code, 'invalid_arguments')
        self.assertEqual(self.agent('domains.get', sid='far')['parts'][0]['name'], '别处节点')
        # The leader guide names domains_get, so a leader has it without searching first.
        self.assertEqual(registry.get('domains.get').core_for, frozenset(('leader',)))

    def test_executions_and_the_git_assistant_cannot_read_the_board(self):
        board.join(self.conn, 'git-admin', 'demo', 'Git 助手', self.root, self.root, 'main', role='leader',
                   group_name='Git 助手', now=NOW)
        self.conn.execute("INSERT INTO git_management(project,enabled_at,base,admin_session_id) "
                          "VALUES('demo',?,'main','git-admin')", (NOW,))
        self.assertEqual(tokens.role_of(self.conn, 's2'), 'execution')
        self.assertEqual(tokens.role_of(self.conn, 'git-admin'), 'git_admin')
        for sid in ('s2', 'git-admin'):
            with self.assertRaises(OpError) as caught:
                self.agent('domains.get', sid=sid)
            self.assertEqual(caught.exception.code, 'forbidden')

    def test_placement_names_the_path_and_what_a_lead_owns(self):
        self.assertEqual(domains.placement(self.conn, 'voice'), '【Crew 架构图】你负责：产品 › 客户端 › 语音。')
        self.assertIsNone(domains.placement(self.conn, 'client'))
        domains.apply(self.conn, 'demo', [{'op': 'assign', 'session_id': 'client', 'domain_id': 'n-client'},
                                          {'op': 'lead', 'session_id': 'client', 'lead': True}], now=NOW)
        self.assertEqual(domains.placement(self.conn, 'client'),
                         '【Crew 架构图】你负责：产品 › 客户端（节点负责人，也负责其下界面、语音共用的部分）。')

    def test_a_changed_placement_is_said_once_and_leaving_the_board_once(self):
        first, line = domains.input_context(self.conn, 'voice', None)
        self.assertEqual(line, first)
        self.assertEqual(domains.input_context(self.conn, 'voice', first), (first, None))
        domains.apply(self.conn, 'demo', [{'op': 'rename', 'id': 'n-voice', 'name': '语音识别'}], now=NOW)
        renamed, line = domains.input_context(self.conn, 'voice', first)
        self.assertEqual(line, '【Crew 架构图】你负责：产品 › 客户端 › 语音识别。')
        domains.apply(self.conn, 'demo', [{'op': 'assign', 'session_id': 'voice', 'domain_id': None}], now=NOW)
        self.assertEqual(domains.input_context(self.conn, 'voice', renamed), (None, '【Crew 架构图】你已不在架构图的任何节点里。'))
        self.assertEqual(domains.input_context(self.conn, 'voice', None), (None, None))

    def test_owned_claude_input_carries_the_placement_on_first_input_and_on_change_only(self):
        manager = chat.Manager(self.home, PLUGIN, lambda: [])
        process = FakeProcess([])
        journal = manager.journal('voice')
        manager.processes['voice'] = chat.Running(process, journal)
        sent = lambda: json.loads(process.stdin.getvalue().splitlines()[-1])['message']['content']
        try:
            manager._send('voice', '按架构图推进')
            self.assertTrue(sent().startswith('【Crew 架构图】你负责：产品 › 客户端 › 语音。\n\n'))
            self.assertEqual([e['text'] for e in journal.events if e['type'] == 'message'], ['按架构图推进'])
            manager._send('voice', '继续')
            self.assertNotIn('Crew 架构图', sent())
            domains.apply(self.conn, 'demo', [{'op': 'parent', 'id': 'n-voice', 'parent_id': 'n-product'}], now=NOW)
            manager._send('voice', '再继续')
            self.assertIn('【Crew 架构图】你负责：产品 › 语音。', sent())
        finally:
            manager.processes.clear(); process.finish(); manager.close()

    def test_owned_codex_input_retries_an_unaccepted_placement(self):
        runtimes.set(self.conn, 'voice', 'codex')
        manager = chat.Manager(self.home, PLUGIN, lambda: [])
        send = mock.Mock(side_effect=[ValueError('fixture rejected input'), 'accepted', 'again'])
        current = SimpleNamespace(runtime='codex', execution=None, send=send, thread_id='fixture-native-thread')
        manager.processes['voice'] = current
        try:
            with self.assertRaisesRegex(ValueError, 'fixture rejected input'):
                manager._send('voice', '推进入口')
            self.assertFalse(hasattr(current, 'domain_context'))
            manager._send('voice', '推进入口')
            self.assertIn('你负责：产品 › 客户端 › 语音', send.call_args.args[0])
            self.assertEqual(send.call_args.kwargs['display_text'], '推进入口')
            manager._send('voice', '接着做')
            self.assertNotIn('Crew 架构图', send.call_args.args[0])
        finally:
            manager.processes.clear(); manager.close()

    def test_an_external_leader_hears_its_place_when_its_window_starts(self):
        context = self.hook('session-start', 'voice')[1]['hookSpecificOutput']['additionalContext']
        self.assertIn('【Crew 架构图】你负责：产品 › 客户端 › 语音。', context)
        tokens.issue(self.conn, session_id='voice', runtime='claude', now=NOW)
        context = self.hook('session-start', 'voice')[1]['hookSpecificOutput']['additionalContext']
        self.assertNotIn('Crew 架构图', context)


if __name__ == '__main__':
    import unittest
    unittest.main()
