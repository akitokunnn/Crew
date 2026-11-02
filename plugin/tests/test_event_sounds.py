"""Real settings storage and HTTP projections; only native model carriers are fake."""
import copy
import tempfile
from pathlib import Path
import unittest
from unittest import mock

import tests
from crew import event_sounds, operations_chat, personal, runtimes, settings
from test_codex_runtime import FakeClient
import test_goal_http as goal_http


class EventSoundsTests(unittest.TestCase):
    def setUp(self):
        self.case = goal_http.GoalHttpTests('test_starting_goal_keeps_general_native_available')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def state(self):
        code, value, _ = self.case.request('/api/state?project=demo')
        self.assertEqual(code, 200, value)
        return value['sounds']

    def test_read_is_unconfigured_and_does_not_create_an_absent_home(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / 'absent'
            self.assertIsNone(event_sounds.get(missing))
            self.assertFalse(missing.exists())
        self.assertIsNone(self.state()['preferences'])
        self.assertEqual(self.state()['events'], [])
        self.assertEqual(len(self.case.processes), 0)

    def test_http_and_desktop_settings_share_saved_preferences_without_starting_models(self):
        before = settings.get(self.case.conn)
        selected = {**event_sounds.DEFAULT, 'leader': 'off', 'assistant': 'knock', 'volume': .41}
        code, value, _ = self.case.request('/api/settings', {'sounds': selected})
        self.assertEqual(code, 200, value)
        self.assertEqual(value['sounds'], selected)
        self.assertEqual(self.state()['preferences'], selected)
        with mock.patch('crew.runtime_catalog.Catalog.refresh', return_value=[]):
            native = operations_chat.settings_snapshot(self.case.home, self.case.server.service.plugin_root)
            self.assertEqual(native['sounds'], selected)
            selected['delivery'] = 'bell'
            operations_chat.save_settings(self.case.home, self.case.server.service.plugin_root, {'sounds': selected})
        self.assertEqual(self.state()['preferences'], selected)
        self.assertEqual(settings.get(self.case.conn), before)
        self.assertEqual(len(self.case.processes), 0)

    def test_invalid_settings_reject_the_entire_write_and_preserve_existing_choices(self):
        self.case.request('/api/settings', {'sounds': event_sounds.DEFAULT})
        original = settings.get(self.case.conn)
        for field, invalid in [('volume', True), ('volume', -1), ('volume', 2), ('volume', float('nan')), ('muted', 'false'), ('leader', 'missing'), ('assistant', None)]:
            with self.subTest(field=field, value=invalid):
                sound = {**event_sounds.DEFAULT, field: invalid}
                ai = copy.deepcopy(original)
                ai['default']['model'] = 'must-not-be-saved'
                code, value, _ = self.case.request('/api/settings', {'ai': ai, 'sounds': sound})
                self.assertEqual(code, 400, value)
                self.assertEqual(settings.get(self.case.conn), original)
                self.assertEqual(event_sounds.get(self.case.home), event_sounds.DEFAULT)

    def completed_reply(self, runtime, goal=False):
        case = self.case
        if runtime == 'codex':
            patcher = mock.patch('crew.codex_runtime._ObservedClient', FakeClient)
            patcher.start()
            self.addCleanup(patcher.stop)
            runtimes.set(case.conn, 's3', 'codex', 'sound-general')
        body = {'session_id': 's3', 'text': '请说明结论', 'message_id': 'sound-input'}
        if goal:
            body['goal_id'] = case.goal()['id']
        code, receipt, _ = case.request('/api/chat/send?project=demo', body)
        self.assertEqual(code, 200, receipt)
        identity = receipt.get('execution_id', 's3')
        current = case.server.service.chat.processes[identity]
        if runtime == 'codex':
            current.client.emit('item/agentMessage/delta', turnId=current.client.turn_id, itemId='sound-answer', delta='结论已经核对。')
        else:
            current.process.emit({'type': 'assistant', 'message': {'id': 'sound-answer', 'content': [{'type': 'text', 'text': '结论已经核对。'}]}})
        self.assertEqual(self.state()['events'], [], 'streaming text cannot signal completion')
        if runtime == 'codex':
            current.client.finish()
        else:
            case.finish_turn(identity, current.process)
        journal = case.server.service.chat.journal(identity)
        with journal.condition:
            self.assertTrue(journal.condition.wait_for(lambda: journal.status == 'idle', timeout=3))
        events = self.state()['events']
        self.assertEqual(len(events), 1, events)
        self.assertEqual(events[0]['kind'], 'leader')
        self.assertEqual(events[0]['scope'], 'reply:' + identity)
        self.assertEqual(self.state()['events'], events, 'polling and history reads keep one stable event')

    def test_claude_general_reply_waits_for_the_terminal_result(self):
        self.completed_reply('claude')

    def test_codex_general_reply_waits_for_the_terminal_result(self):
        self.completed_reply('codex')

    def test_claude_task_reply_keeps_its_execution_scope(self):
        self.completed_reply('claude', goal=True)

    def test_codex_task_reply_keeps_its_execution_scope(self):
        self.completed_reply('codex', goal=True)

    def test_delivery_requires_finalized_finish_and_has_one_report_identity_for_both_carriers(self):
        for runtime in ('claude', 'codex'):
            with self.subTest(runtime=runtime):
                goal, report, execution, _ = self.case.awaiting_acceptance(runtime)
                events = self.state()['events']
                delivered = next(item for item in events if item['scope'] == 'delivery:' + goal['id'])
                self.assertEqual(delivered['id'], 'delivery:' + report['id'])
                self.assertEqual(delivered['kind'], 'delivery')
                self.assertFalse(any(item['scope'] == 'reply:' + execution for item in events))
                self.assertEqual(self.state()['events'], events)

    def test_failed_interrupted_tool_only_and_compaction_events_do_not_complete_a_reply(self):
        text = {'seq': 1, 'type': 'text', 'id': 'partial', 'text': '尚未完成', 'at': '2026-11-02T00:00:00Z'}
        for status in ('failed', 'interrupted'):
            self.assertIsNone(event_sounds.reply_completion([text, {'seq': 2, 'type': 'turn_result', 'status': status}]))
        self.assertIsNone(event_sounds.reply_completion([
            {'seq': 1, 'type': 'assistant', 'id': 'tools', 'content': [{'type': 'tool_use', 'id': 'read', 'name': 'Read'}]},
            {'seq': 2, 'type': 'turn_result', 'status': 'completed', 'success': True}]))
        self.assertIsNone(event_sounds.reply_completion([{'seq': 1, 'type': 'compact'}, {'seq': 2, 'type': 'turn_result', 'status': 'completed'}]))

    def assistant_completion(self, runtime):
        if runtime == 'codex':
            patcher = mock.patch('crew.codex_runtime._ObservedClient', FakeClient)
            patcher.start()
            self.addCleanup(patcher.stop)
            ai = settings.get(self.case.conn)
            ai['assistant'] = {'runtime': 'codex', 'model': None, 'effort': None}
            settings.save(self.case.conn, ai)
        assistant = personal.ensure_assistant(self.case.conn, self.case.home)
        sid = assistant['session_id']
        code, receipt, _ = self.case.request('/api/chat/send', {'session_id': sid, 'text': '请回答', 'message_id': 'assistant-sound'})
        self.assertEqual(code, 200, receipt)
        current = self.case.server.service.chat.processes[sid]
        if runtime == 'codex':
            current.client.emit('item/agentMessage/delta', turnId=current.client.turn_id, itemId='assistant-sound-answer', delta='已经回答。')
            current.client.finish()
        else:
            current.process.emit({'type': 'assistant', 'message': {'id': 'assistant-sound-answer', 'content': [{'type': 'text', 'text': '已经回答。'}]}})
            self.case.finish_turn(sid, current.process)
        events = self.state()['events']
        self.assertEqual([item['kind'] for item in events], ['assistant'])
        code, value, _ = self.case.request('/api/assistant/state')
        self.assertEqual(code, 200, value)
        self.assertEqual(value['sounds']['events'], events)
        self.assertIsNone(value['project'])

    def test_claude_assistant_completion_is_shared_with_project_state(self):
        self.assistant_completion('claude')

    def test_codex_assistant_completion_is_shared_with_project_state(self):
        self.assistant_completion('codex')
