"""Native termination boundaries, using isolated journals and fake carriers only."""
import threading
import unittest
from unittest import mock

import tests
from crew import chat
from test_chat import FakeProcess
from test_usage import UsageFixture


class NativeLifecycleTests(UsageFixture, unittest.TestCase):
    def running(self, *, execution=False):
        manager = chat.Manager(self.home, self.root, lambda: [], process_factory=mock.Mock())
        process = FakeProcess([])
        current = chat.Running(process, manager.journal('s1'))
        if execution:
            current.execution = {'id': 'goal-execution'}
            current.native_id = 'goal-native'
        current.journal.set_status('working')
        current.stderr_reader = threading.Thread(target=manager._stderr, args=(current,))
        current.reader = threading.Thread(target=manager._read, args=('s1', current))
        manager.processes['s1'] = current
        current.stderr_reader.start()
        current.reader.start()
        def finish():
            if process.poll() is None:
                process.finish()
            self.assertTrue(current.reader_done.wait(2))
        self.addCleanup(finish)
        return manager, process, current

    def test_host_termination_exit_one_is_interrupted_in_both_general_and_goal_contexts(self):
        for execution in (False, True):
            with self.subTest(execution=execution):
                manager, process, current = self.running(execution=execution)
                process.terminate = lambda: process.finish(1)  # Windows TerminateProcess uses 1.
                manager.stop_managed('s1', reason='identity_handoff')
                self.assertEqual(current.journal.status, 'offline')
                self.assertFalse([e for e in current.journal.events if e['type'] == 'error'])
                terminal = [e for e in current.journal.events if e['type'] == 'turn_result'][-1]
                self.assertEqual((terminal['status'], terminal['reason']), ('interrupted', 'identity_handoff'))
                self.assertFalse(terminal['success'])

    def test_unexpected_exit_one_keeps_exit_code_and_stderr(self):
        _, process, current = self.running()
        process.stderr.queue.put('native failure details\n')
        process.finish(1)
        self.assertTrue(current.reader_done.wait(2))
        errors = [e['text'] for e in current.journal.events if e['type'] == 'error']
        self.assertEqual(errors, ['Claude 退出（1）：native failure details'])
        self.assertEqual([e['status'] for e in current.journal.events if e['type'] == 'turn_result'], ['failed'])

    def test_clean_exit_without_a_turn_result_is_still_an_unfinished_turn(self):
        _, process, current = self.running()
        process.finish(0)
        self.assertTrue(current.reader_done.wait(2))
        self.assertEqual([e['status'] for e in current.journal.events if e['type'] == 'turn_result'], ['failed'])
        self.assertTrue(any('本轮未完成' in e.get('text', '') for e in current.journal.events))

    def test_failed_terminate_does_not_hide_a_later_real_exit(self):
        manager, process, current = self.running()
        process.terminate = mock.Mock(side_effect=OSError('denied'))
        with self.assertRaisesRegex(OSError, 'denied'):
            manager.stop_managed('s1')
        process.finish(1)
        self.assertTrue(current.reader_done.wait(2))
        self.assertTrue(any('Claude 退出（1）' in e.get('text', '') for e in current.journal.events))

    def test_top_level_result_ends_only_its_turn_without_fabricating_a_tool_result(self):
        journal = chat.Journal(self.home / 'chat' / 'terminal.jsonl')
        for failed in (False, True):
            journal.set_status('working')
            before = len(journal.events)
            journal.consume({'type': 'result', 'is_error': failed, 'errors': ['real reason']})
            terminal = [e for e in journal.events[before:] if e['type'] == 'turn_result']
            self.assertEqual([(e['status'], e['success']) for e in terminal],
                             [('failed' if failed else 'completed', not failed)])
            self.assertFalse([e for e in journal.events[before:] if e['type'] == 'tool_result'])
        journal.set_status('working')
        before = len(journal.events)
        journal.consume({'type': 'result', 'parent_tool_use_id': 'child', 'is_error': False})
        self.assertEqual(journal.status, 'working')
        self.assertFalse([e for e in journal.events[before:] if e['type'] == 'turn_result'])


if __name__ == '__main__':
    unittest.main()
