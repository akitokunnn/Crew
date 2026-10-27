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
        self.controller.executions.busy.return_value = False
        self.controller.executions.maintenance_held.return_value = False
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
        self.controller.executions.busy.return_value = True
        for _ in range(10):
            goal_messages.deliver(self.controller, self.conn)
        self.controller.send.assert_not_called()
        self.assertEqual(self.row(receipt)['attempts'], 0)
        self.assertEqual(self.row(receipt)['status'], 'queued')

    def test_task_request_to_another_leader_uses_their_general_scope(self):
        board.join(self.conn, 'foreign-chief', 'demo', 'foreign', self.root, self.root, 'main', role='leader')
        receipt = goal_messages.queue(self.conn, 'chief-id', 'foreign-chief', 'Please review this interface.',
                                      self.target['id'], kind='decision', message_id='cross-leader')
        self.assertEqual(self.row(receipt)['target_scope'], 'general')
        self.controller.send_input.return_value = {'ok': True, 'accepted': True}
        goal_messages.deliver(self.controller, self.conn)
        self.controller.send.assert_not_called()
        call = self.controller.send_input.call_args
        self.assertEqual(call.args[0]['session_id'], 'foreign-chief')
        self.assertNotIn('_execution', call.args[0])
        self.assertIn(self.active_goal['id'], call.args[1])
        self.assertEqual(self.row(receipt)['status'], 'accepted')

    def test_stopped_target_keeps_scoped_mail_queued_without_spending_attempts(self):
        receipt = self.queue(message_id='stopped-target')
        self.conn.execute('INSERT INTO session_stop_gates VALUES(?,?,?)', ('chief-id', board._time(None), board.HUMAN))
        for _ in range(5):
            goal_messages.deliver(self.controller, self.conn)
        self.controller.send.assert_not_called()
        self.assertEqual(self.row(receipt)['status'], 'queued')
        self.assertEqual(self.row(receipt)['attempts'], 0)

    def test_one_invalid_target_cannot_abort_delivery_to_other_colleagues(self):
        invalid = self.queue(message_id='invalid-target')
        valid = goal_messages.queue(self.conn, 'member-id', 'other-id', 'Independent result', self.sender['id'],
                                    kind='result', message_id='valid-target')
        def current(conn, goal, target):
            if target == 'chief-id':
                raise board.BoardError('Target is no longer available')
            return self.target
        self.controller.current.side_effect = current
        goal_messages.deliver(self.controller, self.conn)
        self.assertEqual(self.row(invalid)['status'], 'failed')
        self.assertEqual(self.row(valid)['status'], 'accepted')

    def external_request(self):
        board.join(self.conn, 'foreign-chief', 'demo', 'foreign', self.root, self.root, 'main', role='leader')
        return goal_messages.queue(self.conn, 'chief-id', 'foreign-chief', 'Review the interface', self.target['id'],
                                   kind='decision', message_id='external-interface')

    def test_explicit_general_reply_returns_to_original_task_not_another_active_scope(self):
        original = self.external_request()
        self.controller.send_input.return_value = {'accepted':True}
        goal_messages.deliver(self.controller, self.conn)
        reply = goal_messages.queue(self.conn, 'foreign-chief', 'chief-id', 'Measured interface result',
                                    kind='result', reply_to=original['message_id'], message_id='interface-reply')
        goal_messages.deliver(self.controller, self.conn)
        self.controller.send.assert_called_once()
        self.assertEqual(self.controller.send.call_args.args[:2], (self.active_goal['id'], 'chief-id'))
        self.assertEqual(self.row(reply)['source_execution'], None)
        self.assertEqual(self.row(reply)['target_scope'], 'goal')
        self.assertEqual(self.row(reply)['status'], 'accepted')
        self.assertEqual(messaging.receipt(self.conn, reply['message_id'])['reply_to'], original['message_id'])

    def test_reply_from_another_task_validates_its_own_source_and_keeps_original_destination(self):
        original = self.external_request()
        self.controller.send_input.return_value = {'accepted':True}
        goal_messages.deliver(self.controller, self.conn)
        other = goals.create(self.conn, 'demo', 'foreign-chief', 'Another task', 'Keep work separate', ['Evidence'])
        goals.transition(self.conn, other['id'], 'running')
        source = goals.create_execution(self.conn, other['id'], 'foreign-chief', runtime='codex')
        reply = goal_messages.queue(self.conn, 'foreign-chief', 'chief-id', 'Cross-task evidence', source['id'],
                                    kind='result', reply_to=original['message_id'], message_id='cross-task-reply')
        goal_messages.deliver(self.controller, self.conn)
        self.assertEqual(self.controller.send.call_args.args[:2], (self.active_goal['id'], 'chief-id'))
        self.assertEqual(self.row(reply)['source_execution'], source['id'])
        self.assertEqual(self.row(reply)['status'], 'accepted')

    def test_reply_to_paused_original_waits_without_resuming_or_using_attempts(self):
        original = self.external_request()
        goals.request_pause(self.conn, self.active_goal['id'], manual=True)
        reply = goal_messages.queue(self.conn, 'foreign-chief', 'chief-id', 'Evidence for paused work', kind='result',
                                    reply_to=original['message_id'], message_id='paused-reply')
        goal_messages.deliver(self.controller, self.conn)
        self.controller.send.assert_not_called()
        self.assertEqual(self.row(reply)['status'], 'queued')
        self.assertEqual(self.row(reply)['attempts'], 0)
        self.assertIn(goals.get(self.conn, self.active_goal['id'])['status'], ('paused','pausing'))

    def test_late_reply_stays_archived_and_cannot_reopen_a_finished_task(self):
        original = self.external_request()
        goals.save_report(self.conn, self.active_goal['id'], self.report('failed'))
        goals.transition(self.conn, self.active_goal['id'], 'failed')
        reply = goal_messages.queue(self.conn, 'foreign-chief', 'chief-id', 'Late result', kind='result',
                                    reply_to=original['message_id'], message_id='late-reply')
        goal_messages.deliver(self.controller, self.conn)
        self.assertEqual(self.row(reply)['status'], 'archived')
        self.assertEqual(goals.get(self.conn, self.active_goal['id'])['current_round'], 1)
        self.controller.send.assert_not_called()

    def test_reply_cannot_claim_someone_elses_original_message(self):
        original = self.external_request()
        with self.assertRaisesRegex(board.BoardError, '原消息'):
            goal_messages.queue(self.conn, 'other-id', 'chief-id', 'Fake reply', kind='result',
                                reply_to=original['message_id'], message_id='fake-reply')

    def test_reply_identity_cannot_be_reused_for_another_original(self):
        first = self.external_request()
        second = goal_messages.queue(self.conn, 'chief-id', 'foreign-chief', 'Another request', self.target['id'],
                                     kind='decision', message_id='another-original')
        goal_messages.queue(self.conn, 'foreign-chief', 'chief-id', 'Same answer', kind='result',
                            reply_to=first['message_id'], message_id='same-reply-id')
        with self.assertRaises(board.Conflict):
            goal_messages.queue(self.conn, 'foreign-chief', 'chief-id', 'Same answer', kind='result',
                                reply_to=second['message_id'], message_id='same-reply-id')

    def test_external_claude_general_hook_receives_cross_leader_mail_once_without_claiming_native_acceptance(self):
        original = self.external_request()
        self.assertTrue(messaging.has_pending_for_hook(self.conn,'foreign-chief'))
        lines = messaging.take_for_hook(self.conn,'foreign-chief')
        self.assertEqual(len(lines),1)
        self.assertIn(self.active_goal['id'],lines[0])
        self.assertIn('--reply-to '+original['message_id'],lines[0])
        self.assertEqual(self.row(original)['status'],'delivered')
        receipt = messaging.receipt(self.conn,original['message_id'])
        self.assertFalse(receipt['accepted'])
        self.assertEqual(messaging.take_for_hook(self.conn,'foreign-chief'),[])

    def test_paused_source_or_ambiguous_native_attempt_cannot_leak_through_a_hook(self):
        original = self.external_request()
        goals.request_pause(self.conn,self.active_goal['id'],manual=True)
        self.assertEqual(messaging.take_for_hook(self.conn,'foreign-chief'),[])
        self.assertEqual(self.row(original)['status'],'queued')
        self.conn.execute('UPDATE goal_message_meta SET attempts=1 WHERE message_id=?',(original['message_id'],))
        self.assertEqual(messaging.take_for_hook(self.conn,'foreign-chief'),[])
        self.assertEqual(self.row(original)['status'],'queued')

    def test_general_hook_never_repeats_unknown_input_and_reconciles_an_accepted_receipt(self):
        receipt = messaging.queue(self.conn,'member-id','chief-id','General evidence',kind='result')
        journal = chat.Journal(self.home/'chat'/'chief-id.jsonl')
        identity = 'peer:'+receipt['message_id']
        journal.emit('input_delivery',message_id=identity,status='unknown')
        self.assertEqual(messaging.take_for_hook(self.conn,'chief-id'),[])
        self.assertEqual(messaging.receipt(self.conn,receipt['message_id'])['status'],'queued')
        journal.emit('input_delivery',message_id=identity,status='accepted')
        self.assertEqual(messaging.take_for_hook(self.conn,'chief-id'),[])
        self.assertTrue(messaging.receipt(self.conn,receipt['message_id'])['accepted'])

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
