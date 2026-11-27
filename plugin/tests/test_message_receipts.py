"""Mail receipts describe persistence, dispatch and native acceptance separately."""
import tests
from tests.ops_helpers import ai_call
import unittest
from unittest import mock

from crew import board, message_receipts, web, goal_messages, goals, messaging, runtimes
from tests.test_goals import GoalFixture
from test_shell import PLUGIN, ShellFixture
import test_mixed_messaging as mixed


class MessageReceiptPresentationTests(unittest.TestCase):
    def test_archive_is_explicitly_not_delivery_or_wake(self):
        receipt = message_receipts.normalize({'ok': True, 'message_id': 'm1', 'kind': 'information', 'status': 'archived'})
        self.assertIn('仅归档', receipt['message'])
        self.assertFalse(receipt['accepted'])

    def test_queued_is_not_a_native_receipt_and_accepted_is_not_a_reply(self):
        queued = message_receipts.normalize({'ok': True, 'message_id': 'm2', 'queued': True, 'kind': 'decision'})
        self.assertIn('尚未交给执行输入', queued['message'])
        accepted = message_receipts.normalize({'ok': True, 'message_id': 'm2', 'status': 'accepted',
                                              'kind': 'decision', 'accepted_at': 'now'})
        self.assertIn('尚不代表模型已读', accepted['message'])

    def test_json_preserves_old_fields_and_adds_explicit_delivery_facts(self):
        receipt = message_receipts.normalize({'ok': True, 'message_id': 'm1', 'queued': False,
                                              'status': 'archived', 'kind': 'information'})
        self.assertTrue(receipt['ok'])
        self.assertFalse(receipt['queued'])
        self.assertFalse(receipt['accepted'])
        self.assertIn('仅归档', receipt['message'])


class GoalMessageReceiptTests(GoalFixture):
    def setUp(self):
        super().setUp()
        self.active_goal = self.goal()
        goals.transition(self.conn, self.active_goal['id'], 'running')
        goals.assign(self.conn, self.active_goal['id'], 'member-id', 'Build the approved format')
        self.leader = goals.create_execution(self.conn, self.active_goal['id'], 'chief-id', runtime='codex')
        self.member = goals.create_execution(self.conn, self.active_goal['id'], 'member-id', runtime='claude')
        self.controller = mock.Mock()
        self.controller.current.return_value = self.member
        self.controller.manager.busy.return_value = False
        self.controller.manager.maintenance_held.return_value = False
        self.controller.send.return_value = {'ok': True, 'accepted': True, 'execution_id': self.member['id']}

    def send(self, kind='information', *, sender='chief-id', key='stable-key', text='The approved format is Markdown'):
        target, execution = ('member-id', self.leader) if sender == 'chief-id' else ('chief-id', self.member)
        return goal_messages.queue(self.conn, sender, target, text, execution['id'], message_id=key, kind=kind)

    def test_information_and_ack_are_archived_without_fabricating_delivery(self):
        for sender in ('chief-id', 'member-id'):
            for kind in ('information', 'ack'):
                receipt = self.send(kind, sender=sender, key=sender + kind)
                peer = self.conn.execute('SELECT * FROM peer_messages WHERE id=?', (receipt['message_id'],)).fetchone()
                self.assertIsNone(peer['delivered_at'])
                self.assertEqual(receipt['status'], 'archived')
                self.assertFalse(receipt['queued'])
                self.assertFalse(receipt['accepted'])
                self.assertIn('仅归档，未投递到执行', receipt['message'])
        goal_messages.deliver(self.controller, self.conn)
        self.controller.send.assert_not_called()
        self.assertEqual(messaging.pending(self.conn), [])

    def test_decision_receipt_becomes_accepted_once_and_replays_after_pause(self):
        queued = self.send('decision')
        self.assertEqual(queued['status'], 'queued')
        self.assertFalse(queued['accepted'])
        goal_messages.deliver(self.controller, self.conn)
        goals.request_pause(self.conn, self.active_goal['id'], manual=True)
        accepted = self.send('decision')
        self.assertEqual(accepted['status'], 'accepted')
        self.assertTrue(accepted['accepted'])
        goal_messages.deliver(self.controller, self.conn)
        self.controller.send.assert_called_once()
        with self.assertRaises(board.Conflict):
            self.send('task')

    def test_failed_attempt_is_visible_and_same_id_retry_preserves_one_delivery(self):
        self.send('decision')
        self.controller.send.side_effect = OSError('temporary fake pipe failure')
        goal_messages.deliver(self.controller, self.conn)
        pending = self.send('decision')
        self.assertEqual(pending['status'], 'queued')
        self.assertIn('temporary fake pipe failure', pending['error'])
        self.assertFalse(pending['accepted'])
        self.controller.send.side_effect = None
        goal_messages.deliver(self.controller, self.conn)
        accepted = self.send('decision')
        self.assertTrue(accepted['accepted'])
        self.assertIsNone(accepted['error'])
        self.assertEqual(self.controller.send.call_count, 2)
        self.assertEqual(self.controller.send.call_args_list[0].kwargs['identity'], self.controller.send.call_args_list[1].kwargs['identity'])

    def test_duplicate_content_is_reported_as_archive_without_new_delivery(self):
        original = self.send('decision')
        goal_messages.deliver(self.controller, self.conn)
        duplicate = self.send('decision', key='different-transport')
        self.assertEqual(duplicate['status'], 'archived')
        self.assertIn(original['message_id'], duplicate['message'])
        self.assertIn('重复', duplicate['message'])
        self.assertFalse(duplicate['accepted'])
        goal_messages.deliver(self.controller, self.conn)
        self.controller.send.assert_called_once()

    def test_codex_native_tool_uses_same_receipt_and_forwards_explicit_kind(self):
        service = web.Service(self.home, PLUGIN, cwd=self.home.parent, agents_provider=lambda: [], isolated_sources=True)
        self.addCleanup(service.close)
        def route(sender, target, text, message_id, *, execution_id, kind, reply_to):
            return goal_messages.queue(self.conn, sender, target, text, execution_id, message_id='tool-key', kind=kind)
        service.chat.send_message = route
        def send():
            return ai_call(service, self.conn, 'chief-id', 'message.send',
                           {'to': 'builder', 'text': 'Approve the format', 'kind': 'decision'},
                           runtime='codex', execution_id=self.leader['id'])['receipt']
        receipt = send()
        self.assertEqual(receipt['kind'], 'decision')
        self.assertEqual(receipt['status'], 'queued')
        self.assertFalse(receipt['accepted'])
        self.assertIn('尚未交给执行输入', receipt['message'])
        goal_messages.deliver(self.controller, self.conn)
        self.assertTrue(send()['accepted'])

    def test_ordinary_codex_legacy_router_never_implies_native_acceptance(self):
        runtimes.set(self.conn, 'member-id', 'codex')
        router = mock.Mock(return_value={'ok': True, 'message_id': 'ordinary', 'queued': True})
        service = web.Service(self.home, PLUGIN, cwd=self.home.parent, agents_provider=lambda: [], isolated_sources=True)
        self.addCleanup(service.close)
        service.chat.send_message = router
        receipt = ai_call(service, self.conn, 'member-id', 'message.send',
                          {'to': 'chief', 'text': 'ordinary evidence'}, runtime='codex')['receipt']
        self.assertEqual(receipt['status'], 'queued')
        self.assertFalse(receipt['accepted'])
        self.assertIn('尚未交给执行输入', receipt['message'])


class OrdinaryMessageReceiptTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.service = web.Service(self.home, PLUGIN, cwd=self.project, agents_provider=lambda: [],
                                   process_factory=self.process_factory, isolated_sources=True)
        self.addCleanup(self.service.close)
        self.manager = self.service.chat

    def attach(self, sid, runtime):
        previous = self.manager.processes.get(sid)
        if previous is not None:
            previous.process.finish()
        return mixed.MixedMessagingTests.attach(self, sid, runtime)

    def test_default_information_and_ack_do_not_wake_either_runtime_or_enter_hook_queue(self):
        for sender, target in (('s3', 's1'), ('s1', 's3')):
            for runtime in ('claude', 'codex'):
                self.attach(target, runtime)
                for kind in ('information', 'ack'):
                    receipt = self.manager.send_message(sender, target, 'Record only ' + runtime + kind,
                                                        message_id=sender + runtime + kind, kind=kind)
                    self.assertEqual(receipt['kind'], kind)
                    self.assertEqual(receipt['status'], 'archived')
                    self.assertFalse(receipt['accepted'])
                    self.assertFalse(receipt['queued'])
                    self.assertIsNone(self.conn.execute('SELECT delivered_at FROM peer_messages WHERE id=?', (receipt['message_id'],)).fetchone()[0])
                    self.assertEqual(messaging.pending(self.conn, target), [])
        with mock.patch.object(self.manager, 'send', side_effect=AssertionError('Archived evidence must not wake')):
            self.manager.deliver_messages()
        self.assertEqual(messaging.take_for_hook(self.conn, 's1'), [])

    def test_explicit_decision_input_receipt_once_across_claude_codex_and_both_roles(self):
        for sender, target in (('s3', 's1'), ('s1', 's3')):
            for sender_runtime in ('claude', 'codex'):
                for target_runtime in ('claude', 'codex'):
                    self.attach(sender, sender_runtime)
                    current = self.attach(target, target_runtime)
                    key = ':'.join((sender, sender_runtime, target_runtime))
                    queued = self.manager.send_message(sender, target, 'Use this approved format ' + key,
                                                       message_id=key, kind='decision')
                    self.assertEqual(queued['status'], 'queued')
                    self.assertFalse(queued['accepted'])
                    self.manager.deliver_messages()
                    receipt = self.manager.send_message(sender, target, 'Use this approved format ' + key,
                                                        message_id=key, kind='decision')
                    self.assertEqual(receipt['status'], 'accepted')
                    self.assertTrue(receipt['accepted'])
                    self.assertIn('尚不代表模型已读', receipt['message'])
                    current.journal.set_status('idle')
                    self.manager.deliver_messages()
                    events = [event for event in current.journal.events if event.get('type') == 'message' and event.get('id') == 'peer:' + queued['message_id']]
                    self.assertEqual(len(events), 1)
                    with self.assertRaises(board.Conflict):
                        self.manager.send_message(sender, target, 'Use this approved format ' + key, message_id=key, kind='result')

    def test_unknown_pipe_result_is_durable_and_never_resubmits_input(self):
        self.attach('s1', 'claude')
        receipt = self.manager.send_message('s3', 's1', 'Apply the decision', message_id='uncertain', kind='decision')
        with mock.patch.object(self.manager, '_send', side_effect=OSError('fake pipe result unknown')) as native:
            for _ in range(3):
                self.manager.deliver_messages()
        self.assertEqual(native.call_count, 1)
        after = messaging.receipt(self.conn, receipt['message_id'])
        self.assertEqual(after['status'], 'queued')
        self.assertFalse(after['accepted'])
        self.assertIn('fake pipe result unknown', after['error'])
        self.assertIsNone(self.conn.execute('SELECT delivered_at FROM peer_messages WHERE id=?', (receipt['message_id'],)).fetchone()[0])

    def test_hook_handoff_and_old_null_kind_queue_do_not_invent_acceptance(self):
        legacy = messaging.queue(self.conn, 's3', 's1', 'Existing legacy queue message', message_id='legacy')
        self.assertIsNone(legacy['kind'])
        self.assertEqual(legacy['status'], 'queued')
        retried = self.manager.send_message('s3', 's1', 'Existing legacy queue message', message_id='legacy')
        self.assertIsNone(retried['kind'])
        self.assertEqual(retried['message_id'], legacy['message_id'])
        lines = messaging.take_for_hook(self.conn, 's1')
        self.assertEqual(len(lines), 1)
        delivered = messaging.receipt(self.conn, legacy['message_id'])
        self.assertEqual(delivered['status'], 'delivered')
        self.assertFalse(delivered['accepted'])
        self.assertIn('Hook', delivered['message'])
        self.assertEqual(messaging.take_for_hook(self.conn, 's1'), [])

    def test_codex_ordinary_tool_explicit_kind_reaches_router_and_default_stays_archive(self):
        sender = self.attach('s3', 'codex')
        self.attach('s1', 'claude')
        for kind, expected in (('decision', 'queued'), (None, 'archived')):
            arguments = {'to': 'one', 'text': 'Use format' if kind else 'For records'}
            if kind:
                arguments['kind'] = kind
            receipt = ai_call(self.service, self.conn, 's3', 'message.send', arguments, runtime='codex')['receipt']
            self.assertEqual(receipt['status'], expected)
            self.assertEqual(receipt['kind'], kind or 'information')

    def test_message_send_preserves_multiline_decision_and_quotes(self):
        body = '批准格式："Markdown"\r\n第二行保持\r\n\n反斜杠 \\n 和空白  \n'
        for original in (body, '采用 "第一版"\n多行资料\n结尾换行\n', '采用 "第一版"\r\n中文 😀 与原始换行\r\n'):
            receipt = ai_call(self.service, self.conn, 's3', 'message.send',
                              {'to': 'one', 'text': original, 'kind': 'decision'})['receipt']
            self.assertEqual((receipt['kind'], receipt['status']), ('decision', 'queued'))
            saved = self.conn.execute('SELECT body FROM peer_messages WHERE id=?', (receipt['message_id'],)).fetchone()
            self.assertEqual(saved[0], original)

    def test_explicit_message_kind_and_stable_request_id(self):
        args = {'to': 'one', 'text': 'Use the approved format', 'kind': 'decision'}
        first = ai_call(self.service, self.conn, 's3', 'message.send', args, request_id='format-v1')
        replay = ai_call(self.service, self.conn, 's3', 'message.send', args, request_id='format-v1')
        self.assertTrue(replay['replayed'])
        self.assertEqual(first['receipt']['kind'], 'decision')
        self.assertEqual(replay['receipt']['message_id'], first['receipt']['message_id'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM peer_messages').fetchone()[0], 1)
