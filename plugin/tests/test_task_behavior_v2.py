"""V2 coordinator actions use stable identity and finite, scoped evidence."""
import tests
import json
import os
import unittest
from unittest import mock

from crew import board, codex_tools, goals, messaging, runtimes, session_ops
from test_shell import ShellFixture


class TaskBehaviorV2Tests(ShellFixture, unittest.TestCase):
    def test_leader_renames_itself_without_replacing_identity_members_or_tasks(self):
        first = goals.create(self.conn, 'demo', 's3', 'Fitness', 'Build a fitness check-in app', ['Save check-ins'])
        second = goals.create(self.conn, 'demo', 's3', 'MIB', 'Measure streaming diarization', ['Final A/B/overlap within 400ms'])
        with mock.patch.dict(os.environ, {'CREW_SESSION_ID': 's3'}):
            code, _, errors = self.run_cli(['rename', 'Fitness and MIB', '--json'])
        self.assertEqual(code, 0, errors)
        self.assertEqual(board.session(self.conn, 's3')['crew_name'], 'Fitness and MIB')
        self.assertEqual(board.session(self.conn, 's1')['group_name'], 'Fitness and MIB')
        self.assertEqual({item['id'] for item in goals.list(self.conn, 'demo')}, {first['id'], second['id']})
        events = self.conn.execute("SELECT COUNT(*) FROM events WHERE kind='session:rename'").fetchone()[0]
        self.assertTrue(session_ops.rename_self(self.conn, 's3', 'Fitness and MIB')['unchanged'])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM events WHERE kind='session:rename'").fetchone()[0], events)
        self.assertEqual(self.processes, [])

    def test_member_cannot_self_promote_rename_leader_or_assign_tasks(self):
        with mock.patch.dict(os.environ, {'CREW_SESSION_ID': 's1'}):
            for args in (['join', 'impostor', '--role', 'leader'],
                         ['--session', 's3', 'rename', 'impostor'], ['rename', 'impostor']):
                with self.subTest(args=args):
                    self.assertNotEqual(self.run_cli(args)[0], 0)
        with self.assertRaisesRegex(board.BoardError, '成员不能继续派工'):
            messaging.queue(self.conn, 's1', 's2', 'Please take over this work', kind='task')
        self.assertEqual(board.session(self.conn, 's1')['role'], 'member')
        self.assertEqual(board.session(self.conn, 's3')['crew_name'], 'lead')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM peer_messages').fetchone()[0], 0)

    def test_codex_rename_uses_bound_leader_and_cannot_name_another_identity(self):
        runtimes.set(self.conn, 's3', 'codex')
        tool = codex_tools.CodexTools(self.home, 's3')
        result = tool({'tool': 'crew_rename', 'arguments': {'name': 'Fitness check-ins'}})
        self.assertTrue(result['success'], result)
        value = json.loads(result['contentItems'][0]['text'])
        self.assertEqual(value['session']['session_id'], 's3')
        rejected = tool({'tool': 'crew_rename', 'arguments': {'name': 'Wrong identity', 'session_id': 's1'}})
        self.assertFalse(rejected['success'])
        self.assertEqual(board.session(self.conn, 's1')['crew_name'], 'one')

    def test_general_member_result_retry_keeps_one_receipt_and_one_delivery(self):
        task = messaging.queue(self.conn, 's3', 's1', 'Check the saved fitness entry', kind='task')
        first = messaging.queue(self.conn, 's1', 's3', 'Saved entry survives restart; fixture evidence.',
                                message_id='result-1', kind='result', reply_to=task['message_id'])
        messaging.mark_delivered(self.conn, first['message_id'], accepted=True)
        retried = messaging.queue(self.conn, 's1', 's3', 'Saved entry survives restart; fixture evidence.',
                                  message_id='result-2', kind='result', reply_to=task['message_id'])
        self.assertEqual(retried['message_id'], first['message_id'])
        self.assertTrue(retried['duplicate'])
        self.assertTrue(retried['accepted'])
        self.assertEqual(messaging.pending(self.conn, 's3'), [])
        # The same wording for a later explicit task is still legitimate new evidence.
        later = messaging.queue(self.conn, 's3', 's1', 'Check the entry after the next change', kind='task')
        fresh = messaging.queue(self.conn, 's1', 's3', 'Saved entry survives restart; fixture evidence.',
                                kind='result', reply_to=later['message_id'])
        self.assertNotEqual(fresh['message_id'], first['message_id'])
        self.assertEqual(len(messaging.pending(self.conn, 's3')), 1)

    def test_ack_and_information_never_wake_the_leader(self):
        for kind, text in (('ack', 'Received'), ('information', 'No new evidence')):
            receipt = messaging.queue(self.conn, 's1', 's3', text, kind=kind)
            self.assertEqual(receipt['status'], 'archived')
        self.assertEqual(messaging.pending(self.conn, 's3'), [])
