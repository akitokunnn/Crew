"""Real settings storage and HTTP projections; only native model carriers are fake."""
import base64
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

    WAV = b'RIFF\x24\x00\x00\x00WAVEfmt ' + bytes(28)

    def upload(self, name='门铃', content=None, filename='bell.wav'):
        body = {'name': name, 'filename': filename, 'data': base64.b64encode(self.WAV if content is None else content).decode()}
        return self.case.request('/api/sounds/custom', body)[:2]

    def test_custom_sound_upload_plays_selects_and_lists_in_every_catalog(self):
        code, value = self.upload()
        self.assertEqual(code, 200, value)
        preset = value['preset']
        self.assertRegex(preset['id'], r'^custom:[0-9a-f]{12}$')
        self.assertEqual((preset['name'], preset['custom']), ('门铃', True))
        self.assertEqual(preset['url'], '/api/sounds/custom/file?id=' + preset['id'])
        code, content, headers = self.case.request(preset['url'], raw=True)
        self.assertEqual((code, content, headers['Content-Type']), (200, self.WAV, 'audio/wav'))
        for header, kind in ((b'OggS' + bytes(20), 'audio/ogg'), (b'ID3' + bytes(20), 'audio/mpeg'), (b'\xff\xfb' + bytes(20), 'audio/mpeg')):
            code, value = self.upload('格式', header, filename='anything.wav')
            self.assertEqual(code, 200, value)
            self.assertEqual(self.case.request(value['preset']['url'], raw=True)[2]['Content-Type'], kind)
        entry = {key: preset[key] for key in ('name', 'url', 'custom')}
        self.assertEqual(self.state()['catalog']['presets'][preset['id']], entry)
        self.assertIn('notes', self.state()['catalog']['presets']['soft'])
        chosen = {**event_sounds.DEFAULT, 'leader': preset['id']}
        code, value, _ = self.case.request('/api/settings', {'sounds': chosen})
        self.assertEqual(code, 200, value)
        self.assertEqual((value['sounds'], value['sound_missing']), (chosen, []))
        self.assertEqual(value['sound_catalog']['presets'][preset['id']], entry)
        self.assertEqual(self.state()['missing'], [])
        self.assertTrue((self.case.home / 'sounds' / 'custom' / (preset['id'][7:] + '.wav')).is_file())
        self.assertEqual(len(self.case.processes), 0)

    def test_custom_sound_rejects_bad_files_and_limits_with_reasons(self):
        for body, reason in (({'name': '', 'filename': 'a.wav', 'data': ''}, '1 到 20'),
                             ({'name': '超' * 21, 'filename': 'a.wav', 'data': ''}, '1 到 20'),
                             ({'name': '坏', 'filename': 'a.wav', 'data': '不是base64'}, 'base64'),
                             ({'name': '假', 'filename': 'a.wav', 'data': base64.b64encode(b'not audio at all').decode()}, 'wav、mp3、ogg'),
                             ({'name': '大', 'filename': 'a.wav', 'data': base64.b64encode(self.WAV + bytes(1024 * 1024)).decode()}, '1MB'),
                             ({'name': '缺', 'data': ''}, 'filename')):
            with self.subTest(reason=reason):
                code, value, _ = self.case.request('/api/sounds/custom', body)
                self.assertEqual(code, 400, value)
                self.assertIn(reason, value['error'])
        for index in range(event_sounds.CUSTOM_LIMIT):
            self.assertEqual(self.upload('音效%d' % index)[0], 200)
        code, value = self.upload('第21个')
        self.assertEqual(code, 400, value)
        self.assertIn('最多 20 个', value['error'])
        code, value, _ = self.case.request('/api/settings', {'sounds': {**event_sounds.DEFAULT, 'delivery': 'custom:000000000000'}})
        self.assertEqual(code, 400, value)
        self.assertIn('不存在', value['error'])

    def test_deleting_a_used_custom_sound_names_its_events_and_a_lost_file_is_reported_missing(self):
        preset = self.upload()[1]['preset']
        chosen = {**event_sounds.DEFAULT, 'leader': preset['id'], 'delivery': preset['id']}
        self.assertEqual(self.case.request('/api/settings', {'sounds': chosen})[0], 200)
        code, value, _ = self.case.request('/api/sounds/custom/delete', {'id': preset['id']})
        self.assertEqual(code, 409, value)
        self.assertIn('leader 回复完成', value['error'])
        self.assertIn('任务交付', value['error'])
        self.assertIn(preset['id'], self.state()['catalog']['presets'])
        # A file removed outside Crew is reported as missing; the saved choice is not swapped for a default.
        (self.case.home / 'sounds' / 'custom' / (preset['id'][7:] + '.wav')).unlink()
        sounds = self.state()
        self.assertEqual((sounds['preferences'], sounds['missing']), (chosen, ['leader', 'delivery']))
        self.assertNotIn(preset['id'], sounds['catalog']['presets'])
        self.assertEqual(self.case.request(preset['url'], raw=True)[0], 400)
        with mock.patch('crew.runtime_catalog.Catalog.refresh', return_value=[]):
            native = operations_chat.settings_snapshot(self.case.home, self.case.server.service.plugin_root)
        self.assertEqual(native['sound_missing'], ['leader', 'delivery'])
        other = self.upload('备用')[1]['preset']
        self.assertEqual(self.case.request('/api/sounds/custom/delete', {'id': other['id']})[:2], (200, {'deleted': other['id']}))
        self.assertNotIn(other['id'], self.state()['catalog']['presets'])
        self.assertFalse(list((self.case.home / 'sounds' / 'custom').glob(other['id'][7:] + '.*')))
        code, value, _ = self.case.request('/api/sounds/custom/delete', {'id': other['id']})
        self.assertEqual(code, 400, value)
        self.assertEqual(self.case.request('/api/sounds/custom/delete', {'id': '../index'})[0], 400)

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
