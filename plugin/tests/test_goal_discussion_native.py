"""Delivered-task discussion works through real guards and a fake native transport."""
from contextlib import closing, redirect_stderr
import io
from pathlib import Path
from unittest import mock

import tests
from crew import board, chat, codex_guard, codex_runtime, goals, hooks, runtimes, store
from tests.test_goals import GoalFixture
from test_codex_runtime import FakeClient
import test_native_approvals as approval_helpers


PLUGIN = Path(__file__).resolve().parents[1]


class GoalDiscussionNativeTests(GoalFixture):
    pending = approval_helpers.NativeApprovalTests.pending

    def prepared(self, runtime):
        (self.home / 'blacklist.txt').write_text('', encoding='utf-8')
        runtimes.set(self.conn, 'chief-id', runtime)
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        report = goals.save_report(self.conn, goal['id'], self.report('awaiting_acceptance'))
        ready = goals.transition(self.conn, goal['id'], 'awaiting_acceptance')
        accepted = goals.review_acceptance(self.conn, goal['id'], True, note='Keep this review.', expected_version=ready['version'])
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime=runtime,
                                           discussion_message_id='explanation-' + runtime)
        return accepted, report, execution

    def test_both_native_guards_allow_discussion_reads_but_edit_requires_same_execution_amend(self):
        for runtime in ('claude', 'codex'):
            with self.subTest(runtime=runtime):
                accepted, report, execution = self.prepared(runtime)
                native = 'discussion-native-' + runtime
                goals.bind_native(self.conn, execution['id'], runtime, native)
                goals.set_execution_status(self.conn, execution['id'], 'running')

                def check(tool, inputs):
                    payload = {'hook_event_name': 'PreToolUse', 'session_id': native, 'cwd': str(self.root),
                               'tool_name': tool, 'tool_input': inputs}
                    if runtime == 'codex':
                        return codex_guard.check(self.home, 'chief-id', payload, execution['id'])
                    output = io.StringIO()
                    with redirect_stderr(output):
                        code, _ = hooks.handle('guard', payload, self.home, PLUGIN,
                            {'CREW_SESSION_ID': 'chief-id', 'CREW_EXECUTION_ID': execution['id']})
                    return output.getvalue() if code else None

                self.assertIsNone(check('Bash', {'command': 'pwd'}))
                edit_tool = 'apply_patch' if runtime == 'codex' else 'Write'
                inputs = ({'command': '*** Begin Patch\n*** Add File: sample.txt\n+hello\n*** End Patch\n'}
                          if runtime == 'codex' else {'file_path': str(self.root / 'sample.txt'), 'content': 'hello'})
                self.assertIn('amend', check(edit_tool, inputs))
                self.assertEqual(goals.get(self.conn, accepted['id']), accepted)
                self.assertEqual(goals.acceptance_review(self.conn, accepted['id'])['note'], 'Keep this review.')
                changed = goals.continue_task(self.conn, accepted['id'], 'Change the label.', actor='chief-id',
                                               discussion_execution_id=execution['id'], expected_version=accepted['version'])
                self.assertIsNone(check(edit_tool, inputs))
                self.assertEqual(changed['current_round'], accepted['current_round'])
                self.assertEqual(goals.get_execution(self.conn, execution['id'])['requirement_version'], changed['requirement_version'])
                self.assertEqual(goals.report_review(self.conn, accepted['id'], report['id'])['note'], 'Keep this review.')

    def test_native_response_preserves_delivery_and_then_uses_fresh_requirement_after_amend(self):
        accepted, report, execution = self.prepared('codex')
        journal = chat.Journal(self.home / 'chat' / (execution['id'] + '.jsonl'))
        with mock.patch('crew.codex_runtime._ObservedClient', FakeClient):
            running = codex_runtime.CodexRunning(self.home, PLUGIN, board.session(self.conn, 'chief-id'),
                {'agent': None}, {'model': None, 'effort': None}, journal, None, execution=execution)
        def close():
            running.close()
            self.assertTrue(running.reader_done.wait(2))
        self.addCleanup(close)
        running.send('Explain only.', 'explain')
        self.assertEqual(goals.get_execution(self.conn, execution['id'])['status'], 'running')
        running.client.finish()
        self.assertEqual(goals.get(self.conn, accepted['id']), accepted)
        self.assertEqual(goals.acceptance_review(self.conn, accepted['id'])['note'], 'Keep this review.')
        changed = goals.continue_task(self.conn, accepted['id'], 'Now change the label.', actor='chief-id',
            discussion_execution_id=execution['id'], expected_version=accepted['version'])
        self.assertNotEqual(execution['requirement_version'], changed['requirement_version'])
        running.send('Continue the small change.', 'change')
        self.assertEqual(goals.get_execution(self.conn, execution['id'])['status'], 'running')
        self.assertEqual(len([call for call in running.client.calls if call[0] == 'turn/start']), 2)
        running.client.finish()
        goals.request_pause(self.conn, accepted['id'], manual=True)
        before = len(journal.events)
        with self.assertRaises(board.Conflict):
            running.send('Must remain paused.', 'no-send')
        self.assertEqual(len(journal.events), before)

    def test_discussion_command_approval_keeps_guard_and_edit_requires_amend(self):
        accepted, _, execution = self.prepared('codex')
        journal = chat.Journal(self.home / 'chat' / (execution['id'] + '.jsonl'))
        with mock.patch('crew.codex_runtime._ObservedClient', FakeClient):
            running = codex_runtime.CodexRunning(self.home, PLUGIN, board.session(self.conn, 'chief-id'),
                {'agent': None}, {'model': None, 'effort': None}, journal, None, execution=execution)
        def close():
            running.close()
            self.assertTrue(running.reader_done.wait(2))
        self.addCleanup(close)
        running.send('Read the existing file and explain it.', 'read-explanation')
        result, done = self.pending(running, running.client, command='Get-Content sample.txt')
        self.assertFalse(done.is_set(), 'A discussion read must reach the user approval instead of auto-declining')
        pending = running.pending_approvals()[0]
        self.assertEqual(running.resolve_approval(pending['id'], True)['status'], 'accepted')
        self.assertTrue(done.wait(2))
        self.assertEqual(result, {'decision': 'accept'})
        self.assertEqual(goals.get(self.conn, accepted['id']), accepted)
        self.assertEqual(goals.acceptance_review(self.conn, accepted['id'])['note'], 'Keep this review.')

        (self.home / 'blacklist.txt').write_text('Bash :: forbidden :: Existing policy still applies\n', encoding='utf-8')
        result, done = self.pending(running, running.client, itemId='blocked-read', command='Get-Content forbidden.txt')
        self.assertTrue(done.wait(2))
        self.assertEqual(result, {'decision': 'decline'})
        self.assertTrue(any('Existing policy still applies' in event.get('approval', {}).get('resolution_reason', '')
                            for event in journal.events))

        for promoted in (False, True):
            if promoted:
                goals.continue_task(self.conn, accepted['id'], 'Change the label.', actor='chief-id',
                    discussion_execution_id=execution['id'], expected_version=accepted['version'])
            item = 'edit-' + str(promoted)
            running.client.emit('item/started', turnId=running.client.turn_id,
                item={'id': item, 'type': 'fileChange', 'status': 'inProgress',
                      'changes': [{'path': str(self.root / 'sample.txt'), 'kind': {'type': 'update'}, 'diff': '-Hello\n+Hi'}]})
            result, done = self.pending(running, running.client, method='item/fileChange/requestApproval',
                                        itemId=item, command=None)
            if promoted:
                pending = running.pending_approvals()[0]
                self.assertEqual(running.resolve_approval(pending['id'], True)['status'], 'accepted')
            self.assertTrue(done.wait(2))
            self.assertEqual(result, {'decision': 'accept' if promoted else 'decline'})
