"""Editable prompts reach actual carrier adapters; fixtures never call a model."""
import tests
from contextlib import closing
import json
import unittest
from unittest import mock

import test_codex_runtime as fixtures
from test_chat import FakeProcess
from crew import behavior_prompts as prompts
from crew import board, channel_digest, chat, personal, personal_actions, rules, runtimes, store
from crew.web_api import behavior_prompts as api


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
        with mock.patch('subprocess.Popen', side_effect=AssertionError('No model')):
            with closing(store.connect(self.home)) as conn:
                assistant = personal.ensure_assistant(conn, self.home)
            values = api.get(self.home, {'scope': 'session', 'identity': assistant['session_id']})
            self.assertEqual(values['scope'], 'session')
            result = api.post(self.home, {'action': 'save', 'key': 'assistant', 'content': 'Use my chosen tone',
                'scope': 'session', 'identity': assistant['session_id'], 'expected_version': 0})
            self.assertFalse(result['started'])
            with closing(store.connect(self.home)) as conn:
                manifest = prompts.for_session(conn, assistant['session_id'])
                self.assertIsNone(manifest['project'])
                self.assertIn('Use my chosen tone', prompts.render(manifest))
                prompts.record_applied(conn, assistant['session_id'], manifest, status='sent')
            self.assertEqual(api.get(self.home, {'action': 'applied', 'scope_id': assistant['session_id']})['loaded'], 'not_confirmed')
            history = api.get(self.home, {'action': 'history', 'key': 'assistant', 'scope': 'session', 'identity': assistant['session_id']})
            self.assertEqual(history['history'][0]['content'], 'Use my chosen tone')
            with self.assertRaises(board.Conflict):
                api.post(self.home, {'action': 'save', 'key': 'assistant', 'content': 'Stale edit', 'scope': 'session',
                    'identity': assistant['session_id'], 'expected_version': 0})

    def test_assistant_prompt_changes_need_original_human_instruction_and_do_not_repeat(self):
        with closing(store.connect(self.home)) as conn:
            assistant = personal.ensure_assistant(conn, self.home)
        request = {'data': {'key': 'common', 'content': 'User chosen rule', 'expected_version': 0},
                   'message_id': 'user-rule', 'request_id': 'rule-operation'}
        read = lambda _: [{'type': 'message', 'id': 'user-rule', 'role': 'user', 'source': 'human',
                           'text': '以后按这条规则工作'}]
        first = personal_actions.dispatch(self.home, assistant['session_id'], 'prompt.save', request, read_events=read)
        repeated = personal_actions.dispatch(self.home, assistant['session_id'], 'prompt.save', request, read_events=read)
        self.assertFalse(first['result']['started_by_operation'])
        self.assertTrue(repeated['duplicate'])
        value = personal_actions.dispatch(self.home, assistant['session_id'], 'prompt.history', {'data': {'key': 'common'}})
        self.assertEqual(len(value['history']), 1)
        self.assertEqual(value['history'][0]['actor'], 'assistant:user-rule')
        with self.assertRaises(board.BoardError):
            personal_actions.dispatch(self.home, assistant['session_id'], 'prompt.reset',
                {**request, 'request_id': 'forged'}, read_events=lambda _: [])

    def test_override_replaces_old_role_reminder_and_summary_behavior(self):
        with closing(store.connect(self.home)) as conn:
            prompts.save(conn, 'member', 'Custom member behavior', expected_version=0)
            self.assertIsNone(rules.reminder(conn, 'worker', self.plugin))
            prompts.save(conn, 'summary', 'Only summarize milestones', expected_version=0)
            manifest = prompts.resolve(conn, project=self.session['project'], keys=('summary',))
        text = channel_digest.prompt('', [], prompts.render(manifest))
        self.assertIn('Only summarize milestones', text)
        self.assertNotIn(prompts.DEFAULTS['summary'], text)
        self.assertTrue(all(heading in text for heading in channel_digest.HEADINGS))


if __name__ == '__main__':
    unittest.main()
