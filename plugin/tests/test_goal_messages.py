"""SQLite replay proves peer evidence does not become an acknowledgement loop."""
import tests
import sqlite3
import unittest
from unittest import mock

from crew import board, chat, codex_client, goal_messages, goals, messaging
from tests.test_goals import GoalFixture


class GoalMessageTests(GoalFixture):
    def setUp(self):
        super().setUp()
        self.active_goal = self.goal()
        goals.transition(self.conn, self.active_goal['id'], 'running')
        self.sender = self.execution(self.active_goal)
        self.target = goals.create_execution(self.conn, self.active_goal['id'], 'chief-id', runtime='codex')
        self.controller = mock.Mock()
        self.controller.current.return_value = self.target
        self.controller.manager.busy.return_value = False
        self.controller.send.return_value = {'ok': True, 'accepted': True, 'execution_id': self.target['id']}

    def execution(self, goal):
        goals.assign(self.conn, goal['id'], 'member-id', 'Measure repeatable evidence')
        return goals.create_execution(self.conn, goal['id'], 'member-id', runtime='codex')

    def queue(self, text='Measured result', *, kind='result', message_id=None, execution=None):
        return goal_messages.queue(self.conn, 'member-id', 'chief-id', text,
                                   (execution or self.sender)['id'], kind=kind, message_id=message_id)

    def row(self, receipt):
        return dict(self.conn.execute('SELECT * FROM goal_message_meta WHERE message_id=?',
                                      (receipt['message_id'],)).fetchone())

    def test_300_ack_replay_never_wakes_native_or_enters_legacy_delivery(self):
        for index in range(300):
            self.queue('Received confirmation ' + str(index), kind='ack', message_id='ack-' + str(index))
        manager = chat.Manager(self.home, self.root, agents_provider=lambda: [],
                               process_factory=mock.Mock(side_effect=AssertionError('No native wake is allowed')))
        manager.goal_controller = self.controller
        with (mock.patch.object(manager, '_resume', side_effect=AssertionError('No legacy wake')),
              mock.patch.object(manager, '_send', side_effect=AssertionError('No legacy input'))):
            for _ in range(5):
                manager.deliver_messages()
        self.controller.send.assert_not_called()
        manager.process_factory.assert_not_called()
        self.assertEqual(messaging.pending(self.conn), [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM goal_message_meta WHERE status='archived'").fetchone()[0], 300)
        self.assertEqual(self.conn.execute('SELECT SUM(attempts) FROM goal_message_meta').fetchone()[0], 0)

    def test_same_result_with_new_transport_message_id_only_wakes_once(self):
        original = self.queue('Sample A: short handover still fails', message_id='first')
        goal_messages.deliver(self.controller, self.conn)
        duplicate = self.queue('Sample A: short handover still fails', message_id='changed-transport')
        for _ in range(4):
            goal_messages.deliver(self.controller, self.conn)
        self.assertEqual(self.controller.send.call_count, 1)
        self.assertEqual(self.row(original)['status'], 'accepted')
        self.assertEqual(self.row(duplicate)['status'], 'archived')
        self.assertEqual(self.row(duplicate)['attempts'], 0)
        self.assertEqual(messaging.pending(self.conn), [])

    def test_ten_new_results_are_one_bounded_native_input_and_no_summary_model(self):
        receipts = [self.queue('Sample ' + str(index) + ' has new evidence', message_id='sample-' + str(index))
                    for index in range(10)]
        goal_messages.deliver(self.controller, self.conn)
        self.controller.send.assert_called_once()
        text = self.controller.send.call_args.args[2]
        for index in range(10):
            self.assertIn('Sample ' + str(index) + ' has new evidence', text)
        self.assertIn('不代表用户授权', text)
        self.assertEqual(self.controller.send.call_args.kwargs['source'], 'peer')
        self.assertTrue(all(self.row(receipt)['status'] == 'accepted' for receipt in receipts))
        goal_messages.deliver(self.controller, self.conn)
        self.controller.send.assert_called_once()

    def test_targets_and_goals_are_never_batched_together(self):
        other = self.goal()
        goals.transition(self.conn, other['id'], 'running')
        other_sender = self.execution(other)
        self.queue('Goal one evidence', message_id='one')
        self.queue('Goal two evidence', message_id='two', execution=other_sender)
        goal_messages.deliver(self.controller, self.conn)
        self.assertEqual(self.controller.send.call_count, 2)
        calls = {call.args[0]: call.args[2] for call in self.controller.send.call_args_list}
        self.assertIn('Goal one evidence', calls[self.active_goal['id']])
        self.assertNotIn('Goal two evidence', calls[self.active_goal['id']])
        self.assertIn('Goal two evidence', calls[other['id']])
        self.assertNotIn('Goal one evidence', calls[other['id']])

    def test_user_pause_defers_without_spending_attempts_and_version_change_archives(self):
        receipt = self.queue(message_id='pending-before-pause')
        goals.request_pause(self.conn, self.active_goal['id'], manual=True)
        for _ in range(5):
            goal_messages.deliver(self.controller, self.conn)
        self.controller.send.assert_not_called()
        self.assertEqual(self.row(receipt)['status'], 'queued')
        self.assertEqual(self.row(receipt)['attempts'], 0)
        goals.amend(self.conn, self.active_goal['id'], 'User changed the acceptance requirement')
        goal_messages.deliver(self.controller, self.conn)
        self.assertEqual(self.row(receipt)['status'], 'archived')
        self.controller.send.assert_not_called()

    def test_old_round_is_archived_and_only_new_round_evidence_wakes(self):
        old = self.queue('Same wording across separate rounds', message_id='old-round')
        goals.save_report(self.conn, self.active_goal['id'], self.report('failed'))
        goals.transition(self.conn, self.active_goal['id'], 'failed')
        reopened = goals.reopen(self.conn, self.active_goal['id'], 'Retry in a new delivery round')
        goals.transition(self.conn, reopened['id'], 'running')
        sender = self.execution(reopened)
        new = self.queue('Same wording across separate rounds', message_id='new-round', execution=sender)
        goal_messages.deliver(self.controller, self.conn)
        self.controller.send.assert_called_once()
        self.assertEqual(self.row(old)['status'], 'archived')
        self.assertEqual(self.row(new)['status'], 'accepted')
        self.assertNotIn('消息' + old['message_id'], self.controller.send.call_args.args[2])

    def test_unknown_native_receipt_stops_after_three_same_identity_attempts_and_error_is_visible(self):
        receipt = self.queue(message_id='unknown-native')
        self.controller.send.side_effect = codex_client.TransportError('socket closed after write; acceptance unknown')
        for _ in range(20):
            goal_messages.deliver(self.controller, self.conn)
        self.assertEqual(self.controller.send.call_count, 3)
        row = self.row(receipt)
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(row['attempts'], 3)
        self.assertIn('TransportError: socket closed', row['error'])
        self.assertEqual(len({call.kwargs['identity'] for call in self.controller.send.call_args_list}), 1)
        peer = self.conn.execute('SELECT * FROM peer_messages WHERE id=?', (receipt['message_id'],)).fetchone()
        self.assertIsNone(peer['delivered_at'])
        self.assertIn('TransportError:', peer['error'])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE kind='goal:delivery_failed'").fetchone()[0], 1)

    def test_retry_batch_does_not_absorb_new_messages_or_change_unknown_input(self):
        first = self.queue('First uncertain result', message_id='uncertain')
        self.controller.send.side_effect = OSError('unknown receipt')
        goal_messages.deliver(self.controller, self.conn)
        original = self.controller.send.call_args
        second = self.queue('New result after uncertainty', message_id='new-result')
        goal_messages.deliver(self.controller, self.conn)
        self.assertEqual(self.controller.send.call_count, 2)
        self.assertEqual(self.controller.send.call_args, original)
        self.assertEqual(self.row(second)['attempts'], 0)
        self.controller.send.side_effect = None
        goal_messages.deliver(self.controller, self.conn)
        self.assertEqual(self.row(first)['status'], 'accepted')
        self.assertEqual(self.row(second)['attempts'], 0)
        goal_messages.deliver(self.controller, self.conn)
        self.assertEqual(self.row(second)['status'], 'accepted')

    def test_unconfirmed_send_result_is_not_marked_as_native_accepted(self):
        receipt = self.queue(message_id='no-native-receipt')
        self.controller.send.return_value = {'ok': True, 'accepted': False, 'delivery': 'unknown'}
        for _ in range(3):
            goal_messages.deliver(self.controller, self.conn)
        self.assertEqual(self.row(receipt)['status'], 'failed')
        self.assertIn('NativeReceiptUnknown', self.row(receipt)['error'])
        self.assertIsNone(self.row(receipt)['accepted_at'])

    def test_real_manager_unknown_journal_retries_never_resubmit_native_input(self):
        receipt = self.queue(message_id='native-write-unknown')
        manager = chat.Manager(self.home, self.root, agents_provider=lambda: [],
                               process_factory=mock.Mock(side_effect=AssertionError('No model process')))
        target = {**board.session(self.conn, 'chief-id'), '_storage_id': self.target['id']}
        manager.journal(self.target['id']).set_status('idle')
        self.controller.send.side_effect = lambda goal, person, text, **options: manager.send(target, text, **options)
        with (mock.patch.object(manager, 'running', return_value=True),
              mock.patch.object(manager, '_send', side_effect=codex_client.TransportError('lost native reply')) as native):
            for _ in range(10):
                goal_messages.deliver(self.controller, self.conn)
        self.assertEqual(native.call_count, 1)
        self.assertEqual(self.controller.send.call_count, 3)
        self.assertEqual(self.row(receipt)['status'], 'failed')
        journal = chat.Journal(manager.path(self.target['id']))
        identity = self.controller.send.call_args.kwargs['identity']
        self.assertEqual(journal.inputs[identity]['status'], 'unknown')

    def test_busy_target_waits_without_spending_delivery_budget(self):
        receipt = self.queue(message_id='busy-target')
        self.controller.manager.busy.return_value = True
        for _ in range(10):
            goal_messages.deliver(self.controller, self.conn)
        self.controller.send.assert_not_called()
        self.assertEqual(self.row(receipt)['attempts'], 0)
        self.assertEqual(self.row(receipt)['status'], 'queued')

    def test_nonleader_cannot_send_task_or_impersonate_another_execution(self):
        with self.assertRaises(board.BoardError):
            self.queue('Do work without leader authority', kind='task')
        with self.assertRaises(board.BoardError):
            goal_messages.queue(self.conn, 'member-id', 'other-id', 'Impersonation', self.target['id'], kind='result')
        self.controller.send.assert_not_called()

    def test_failed_metadata_insert_rolls_back_peer_record_and_cannot_leak_to_legacy_queue(self):
        self.conn.execute("CREATE TEMP TRIGGER reject_goal_meta BEFORE INSERT ON goal_message_meta "
                          "BEGIN SELECT RAISE(ABORT,'fixture metadata failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'fixture metadata failure'):
            self.queue(message_id='atomic-goal-message')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM peer_messages').fetchone()[0], 0)
        self.assertEqual(messaging.pending(self.conn), [])


if __name__ == '__main__':
    unittest.main()
