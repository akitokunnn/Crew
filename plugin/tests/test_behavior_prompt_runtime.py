"""Editable prompts reach actual carrier adapters; fixtures never call a model."""
import tests
from contextlib import closing
import json
import unittest
from unittest import mock

import test_codex_runtime as fixtures
from test_chat import FakeProcess
from test_core import operation_service
from tests.ops_helpers import ai_call
from crew import behavior_prompts as prompts
from crew import board, channel_digest, chat, personal, runtimes, store


class BehaviorPromptRuntimeTests(unittest.TestCase):
    setUp = fixtures.CodexRuntimeTests.setUp
    make_running = fixtures.CodexRuntimeTests.make_running

    def test_codex_edit_applies_on_next_input_once_and_preserves_the_old_manifest(self):
        manager = chat.Manager(self.home, self.plugin, lambda: [])
        self.addCleanup(manager.close)
        running, client = self.make_running()
        manager.processes['worker'] = running
        with closing(store.connect(self.home)) as conn:
            before = prompts.inspect_applied(conn, 'worker')['applications'][0]
            prompts.save(conn, 'common', 'Updated behavior for this fixture', expected_version=0)
        self.assertNotIn('turn/start', [method for method, _ in client.calls])
        manager.send(self.session, 'Proceed', identity='input-one')
        text = [params for method, params in client.calls if method == 'turn/start'][-1]['input'][-1]['text']
        self.assertIn('Updated behavior for this fixture', text)
        client.finish()
        manager.send(self.session, 'Continue', identity='input-two')
        text = [params for method, params in client.calls if method == 'turn/start'][-1]['input'][-1]['text']
        self.assertNotIn('Updated behavior for this fixture', text)
        with closing(store.connect(self.home)) as conn:
            current = prompts.inspect_applied(conn, 'worker')
            self.assertEqual(current['loaded'], 'confirmed')
            self.assertEqual(len(current['applications']), 2)
            self.assertEqual(current['applications'][0], before)
            self.assertIn('turn/start', current['applications'][-1]['evidence'])
        self.assertEqual([event['text'] for event in manager.journal('worker').events if event['type'] == 'message'], ['Proceed', 'Continue'])

    def test_failed_codex_input_does_not_confirm_updated_prompt(self):
        manager = chat.Manager(self.home, self.plugin, lambda: [])
        self.addCleanup(manager.close)
        running, client = self.make_running()
        manager.processes['worker'] = running
        with closing(store.connect(self.home)) as conn:
            prompts.save(conn, 'common', 'New instruction', expected_version=0)
        client.fail_turn = True
        with self.assertRaisesRegex(RuntimeError, 'start refused'):
            manager.send(self.session, 'Retain my text', identity='failed-input')
        with closing(store.connect(self.home)) as conn:
            value = prompts.inspect_applied(conn, 'worker')
            self.assertEqual(value['loaded'], 'not_confirmed')
            self.assertEqual(value['applications'][-1]['status'], 'prepared')

    def test_claude_initial_prompt_and_native_user_receipt_have_distinct_evidence(self):
        manager = chat.Manager(self.home, self.plugin, lambda: [], process_factory=FakeProcess)
        self.addCleanup(manager.close)
        with closing(store.connect(self.home)) as conn:
            runtimes.set(conn, 'worker', 'claude', native_id='worker')
        manager.journal('worker').set_status('idle')
        with closing(store.connect(self.home)) as conn:
            spec, _ = manager._resume_spec(conn, self.session)
        with mock.patch('crew.chat.claude_executable', return_value='fake-claude'):
            manager._launch_native(self.session, spec, resume=True)
        current = manager.processes['worker']
        self.assertEqual(current.process.command.count('--append-system-prompt'), 1)
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(prompts.inspect_applied(conn, 'worker')['loaded'], 'not_confirmed')
            prompts.save(conn, 'common', 'CC updated fixture rule', expected_version=0)
        manager.send(self.session, 'CC message', identity='cc-message')
        body = [json.loads(line) for line in current.process.stdin.getvalue().splitlines() if json.loads(line).get('type') == 'user'][-1]
        self.assertIn('CC updated fixture rule', body['message']['content'])
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(prompts.inspect_applied(conn, 'worker')['applications'][-1]['status'], 'sent')
        current.process.emit({'type': 'user', 'uuid': 'cc-message', 'session_id': 'worker',
                              'message': {'role': 'user', 'content': body['message']['content']}})
        manager.stop_managed('worker')
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(prompts.inspect_applied(conn, 'worker')['applications'][-1]['status'], 'confirmed')

    def test_http_adapter_scopes_history_and_projectless_assistant_never_launch_ai(self):
        service = operation_service(self, self.home)
        with mock.patch('subprocess.Popen', side_effect=AssertionError('No model')):
            with closing(store.connect(self.home)) as conn:
                assistant = personal.ensure_assistant(conn, self.home)
            values = service.op('prompt.list', {'scope': 'session', 'identity': assistant['session_id']})
            self.assertEqual(values['scope'], 'session')
            result = service.op('prompt.save', {'key': 'assistant', 'content': 'Use my chosen tone',
                'scope': 'session', 'identity': assistant['session_id'], 'expected_version': 0})
            self.assertFalse(result['started'])
            with closing(store.connect(self.home)) as conn:
                manifest = prompts.for_session(conn, assistant['session_id'])
                self.assertIsNone(manifest['project'])
                self.assertIn('Use my chosen tone', prompts.render(manifest))
                prompts.record_applied(conn, assistant['session_id'], manifest, status='sent')
            self.assertEqual(service.op('prompt.applied', {'scope_id': assistant['session_id']})['loaded'], 'not_confirmed')
            history = service.op('prompt.history', {'key': 'assistant', 'scope': 'session', 'identity': assistant['session_id']})
            self.assertEqual(history['history'][0]['content'], 'Use my chosen tone')
            with self.assertRaises(board.Conflict):
                service.op('prompt.save', {'key': 'assistant', 'content': 'Stale edit', 'scope': 'session',
                    'identity': assistant['session_id'], 'expected_version': 0})

    def test_assistant_prompt_changes_need_original_human_instruction_and_do_not_repeat(self):
        service = operation_service(self, self.home)
        with closing(store.connect(self.home)) as conn:
            sid = personal.ensure_assistant(conn, self.home)['session_id']
            service.chat.journal(sid).message('user-rule', '以后按这条规则工作', source='human')
            args = {'key': 'common', 'content': 'User chosen rule', 'expected_version': 0}
            proof = {'request_id': 'rule-operation', 'message_id': 'user-rule'}
            first = ai_call(service, conn, sid, 'prompt.save', args, **proof)
            repeated = ai_call(service, conn, sid, 'prompt.save', args, **proof)
            self.assertFalse(first['result']['started_by_operation'])
            self.assertTrue(repeated['replayed'])
            value = ai_call(service, conn, sid, 'prompt.history', {'key': 'common'})
            self.assertEqual(len(value['history']), 1)
            self.assertEqual(value['history'][0]['actor'], 'assistant:user-rule')
            with self.assertRaises(board.BoardError):
                ai_call(service, conn, sid, 'prompt.reset', {'key': 'common', 'expected_version': 1},
                        message_id='missing')

    def test_override_replaces_member_and_summary_behavior(self):
        with closing(store.connect(self.home)) as conn:
            prompts.save(conn, 'member', 'Custom member behavior', expected_version=0)
            self.assertIn('Custom member behavior', prompts.render(prompts.for_session(conn, 'worker')))
            prompts.save(conn, 'summary', 'Only summarize milestones', expected_version=0)
            manifest = prompts.resolve(conn, project=self.session['project'], keys=('summary',))
        text = channel_digest.prompt('', [], prompts.render(manifest))
        self.assertIn('Only summarize milestones', text)
        self.assertNotIn(prompts.DEFAULTS['summary'], text)
        self.assertTrue(all(heading in text for heading in channel_digest.HEADINGS))


if __name__ == '__main__':
    unittest.main()
