"""Codex discussion isolation against static app-server protocol fixtures."""
import tests
import json
import unittest
from unittest import mock

from crew import board, codex_discussion


def configuration():
    return {'config': {'mcp_servers': {'user-server': {'command': 'SECRET-COMMAND', 'env': {'TOKEN': 'SECRET-TOKEN'}}}},
            'origins': {'mcp_servers': {'name': {'type': 'user', 'file': 'native/config.toml'}, 'version': 'u1'}},
            'layers': [{'name': {'type': 'project', 'dotCodexFolder': '/isolated/project/.codex'},
                        'version': 'p1', 'config': {'mcp_servers': {'project-server': {'url': 'https://private.invalid?token=SECRET'}}}}]}


class CodexDiscussionTests(unittest.TestCase):
    def test_effective_and_project_layers_disable_all_servers_without_copying_credentials(self):
        client = mock.Mock()
        client.request.return_value = configuration()
        original = {'hooks.preToolUse': [{'matcher': 'Bash', 'hooks': []}]}
        config, proof = codex_discussion.prepare(client, '/isolated/project', original)
        self.assertEqual(config['mcp_servers'], {'user-server': {'enabled': False}, 'project-server': {'enabled': False}})
        self.assertEqual(config['hooks.preToolUse'], original['hooks.preToolUse'])
        self.assertNotIn('mcp_servers', original)
        self.assertNotIn('SECRET', json.dumps([config, proof]))
        self.assertEqual(proof['layers'], [{'type': 'project', 'version': 'p1'}])
        client.request.assert_called_once_with('config/read', {'includeLayers': True, 'cwd': '/isolated/project'}, timeout=5)

    def test_missing_effective_layers_is_a_visible_failure(self):
        for response in (None, {}, {'config': {}, 'origins': {}}, {'config': {}, 'origins': {}, 'layers': None},
                         {'config': {}, 'origins': {}, 'layers': [{'config': {}, 'name': {'type': 'user'}}]}):
            with self.subTest(response=response):
                client = mock.Mock()
                client.request.return_value = response
                with self.assertRaises(board.BoardError):
                    codex_discussion.prepare(client, '/isolated/project', {})

    def test_native_read_error_is_not_replaced_with_an_unrestricted_configuration(self):
        client = mock.Mock()
        client.request.side_effect = OSError('native effective configuration unavailable')
        with self.assertRaisesRegex(OSError, 'effective configuration unavailable'):
            codex_discussion.prepare(client, '/isolated/project', {})

    def test_verified_disabled_mcp_inventory_is_thread_scoped_and_complete(self):
        client = mock.Mock()
        client.request.side_effect = [{'data': [{'name': 'user-server', 'runtimeStatus': 'disabled'}], 'nextCursor': 'page2'},
                                     {'data': [{'name': 'project-server', 'runtimeStatus': 'disabled'}], 'nextCursor': None}]
        proof = codex_discussion.verify(client, 'discussion-thread', {'sandbox': {'type': 'readOnly', 'networkAccess': False}})
        self.assertEqual(proof['status'], 'confirmed')
        self.assertEqual(proof['disabled_servers'], ['user-server', 'project-server'])
        self.assertTrue(all(call.args[1]['threadId'] == 'discussion-thread' for call in client.request.call_args_list))
        self.assertEqual(client.request.call_args_list[-1].args[1]['cursor'], 'page2')

    def test_empty_complete_inventory_and_readonly_network_default_are_supported(self):
        client = mock.Mock()
        client.request.return_value = {'data': [], 'nextCursor': None}
        self.assertEqual(codex_discussion.verify(client, 'discussion', {'sandbox': {'type': 'readOnly'}})['status'], 'confirmed')

    def test_writable_or_network_enabled_sandbox_rejects_before_any_mcp_or_model_call(self):
        for policy in ({'type': 'workspaceWrite', 'networkAccess': False}, {'type': 'readOnly', 'networkAccess': True},
                       {'type': 'dangerFullAccess'}, None):
            with self.subTest(policy=policy):
                client = mock.Mock()
                with self.assertRaisesRegex(board.BoardError, '只读讨论沙箱'):
                    codex_discussion.verify(client, 'discussion', {'sandbox': policy})
                client.request.assert_not_called()

    def test_connected_unstarted_or_unknown_mcp_is_not_assumed_disabled(self):
        for status in ('connected', 'notStarted', 'starting', 'failed', None):
            with self.subTest(status=status):
                client = mock.Mock()
                client.request.return_value = {'data': [{'name': 'side-effects', 'runtimeStatus': status}], 'nextCursor': None}
                with self.assertRaisesRegex(board.BoardError, 'side-effects'):
                    codex_discussion.verify(client, 'discussion')

    def test_pagination_repetition_and_invalid_inventory_fail_without_guessing(self):
        client = mock.Mock()
        client.request.return_value = {'data': [], 'nextCursor': 'repeat'}
        with self.assertRaisesRegex(board.BoardError, '分页重复'):
            codex_discussion.verify(client, 'discussion')
        self.assertEqual(client.request.call_count, 2)
        client.request.return_value = None
        with self.assertRaisesRegex(board.BoardError, '完整 MCP 状态'):
            codex_discussion.verify(client, 'discussion')


if __name__ == '__main__':
    unittest.main()
