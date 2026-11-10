"""Handing a task to another leader: records, waiting for running turns, notices and the handoff material."""
from types import SimpleNamespace
from unittest import mock

import tests
import io
import os
from contextlib import redirect_stderr, redirect_stdout

from crew import board, cli, goal_actions, goal_runtime, goal_work, goals
from tests.test_goal_actions import Host
from tests.test_goals import GoalFixture


class FakeManager:
    def __init__(self, home):
        self.home, self.busy_keys, self.running_keys, self.stopped, self.journals = home, set(), set(), [], {}
        self.processes = {}

    def busy(self, key):
        return key in self.busy_keys

    def running(self, key):
        return key in self.running_keys

    def stop_managed(self, key, reason='managed_stop'):
        self.stopped.append(key)
        self.running_keys.discard(key)

    def journal(self, key):
        return self.journals.setdefault(key, SimpleNamespace(events=[]))


class TransferFixture(GoalFixture):
    def setUp(self):
        super().setUp()
        board.join(self.conn, 'next-id', 'demo', 'next', self.root, self.root, 'main', role='leader')
        board.join(self.conn, 'next-member', 'demo', 'next-worker', self.root, self.root, 'main', group_name='next')

    def archive(self, session_id):
        self.conn.execute("INSERT INTO session_controls(session_id,archived_status) VALUES(?,'done')", (session_id,))
        self.assertTrue(board.is_archived(self.conn, session_id))

    def notices(self, session_id):
        return [dict(row) for row in self.conn.execute('SELECT text,wake,sent_at FROM leader_notices WHERE session_id=? ORDER BY id',
                                                      (session_id,))]


class GoalTransferTests(TransferFixture):
    def test_running_task_waits_for_the_turn_then_moves_people_work_and_branches(self):
        goal = self.goal()
        goals.assign(self.conn, goal['id'], 'member-id', 'Measure short inputs')
        goal_work.add(self.conn, goal['id'], 'measure', 'Measure', 'Numbers recorded', owner='member-id')
        goal_work.add(self.conn, goal['id'], 'setup', 'Setup', 'Ready')
        goal_work.update(self.conn, goal['id'], 'setup', 1, status='done', note='Done with evidence')
        goals.transition(self.conn, goal['id'], 'running')
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='claude')
        goals.set_execution_status(self.conn, execution['id'], 'running')
        goal_work.update(self.conn, goal['id'], 'measure', 1, status='doing', actor='member-id')
        self.conn.execute("INSERT INTO branch_claims VALUES('demo','feature/speaker','chief-id',?,'2026-01-01T00:00:00')", (goal['id'],))

        transfer = goals.request_transfer(self.conn, goal['id'], 'next-id', 'Half measured; see measurements/.', actor='chief-id')
        self.assertEqual(transfer['status'], 'pending')
        self.assertEqual(goals.request_transfer(self.conn, goal['id'], 'next-id', 'again', actor='chief-id')['id'], transfer['id'])
        gate = goals.dispatch_check(self.conn, goal['id'])
        self.assertFalse(gate['allowed'])
        self.assertIn('next', gate['reason'])
        self.assertFalse(goals.finalize_transfer(self.conn, goal['id'])['finalized'])
        self.assertEqual(goals.get(self.conn, goal['id'])['leader_session_id'], 'chief-id')

        goals.set_execution_status(self.conn, execution['id'], 'idle')
        result = goals.finalize_transfer(self.conn, goal['id'])
        self.assertTrue(result['finalized'])
        value = goals.get(self.conn, goal['id'])
        self.assertEqual((value['leader_session_id'], value['status']), ('next-id', 'queued'))
        self.assertEqual({item['session_id']: item['status'] for item in value['assignments']},
                         {'chief-id': 'released', 'member-id': 'released', 'next-id': 'assigned'})
        self.assertEqual(goals.get_execution(self.conn, execution['id'])['status'], 'cancelled')
        work = {item['key']: item for item in goal_work.items(self.conn, goal['id'])}
        self.assertEqual((work['measure']['owner_session_id'], work['measure']['status']), ('next-id', 'planned'))
        self.assertEqual((work['setup']['owner_session_id'], work['setup']['status']), ('chief-id', 'done'))
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM branch_claims WHERE branch='feature/speaker' AND session_id='next-id'").fetchone())
        notice = self.notices('next-id')
        self.assertEqual(len(notice), 1)
        self.assertEqual(notice[0]['wake'], 1)
        self.assertIn('Half measured', notice[0]['text'])
        self.assertIn('crew goal start ' + goal['id'], notice[0]['text'])
        self.assertEqual(self.notices('chief-id'), [])  # The giver asked for it itself.
        self.assertTrue(goals.dispatch_check(self.conn, goal['id'])['allowed'])
        # The new leader can now run and staff the task from its own group.
        goals.assign(self.conn, goal['id'], 'next-member', 'Finish the measurements', actor='next-id')
        with self.assertRaises(board.BoardError):
            goals.assign(self.conn, goal['id'], 'member-id', 'Old group', actor='next-id')
        with self.assertRaises(board.BoardError):
            goals.amend(self.conn, goal['id'], 'old leader edit', actor='chief-id')

    def test_user_handover_of_a_waiting_task_keeps_it_waiting_and_tells_both_without_waking(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        goals.request_pause(self.conn, goal['id'])
        goals.request_transfer(self.conn, goal['id'], 'next-id', '')
        self.assertTrue(goals.finalize_transfer(self.conn, goal['id'])['finalized'])
        value = goals.get(self.conn, goal['id'])
        self.assertEqual((value['leader_session_id'], value['status'], value['manual_pause']), ('next-id', 'paused', True))
        self.assertEqual([item['wake'] for item in self.notices('next-id')], [0])
        self.assertIn('用户的安排', self.notices('next-id')[0]['text'])
        self.assertEqual([item['wake'] for item in self.notices('chief-id')], [0])
        self.assertIn('next', self.notices('chief-id')[0]['text'])

    def test_task_stopped_only_because_its_leader_left_resumes_with_the_new_leader(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        self.archive('chief-id')
        goals.request_pause(self.conn, goal['id'], actor=goals._OPERATIONS, reason=goals.LEADER_GONE)
        goals.request_transfer(self.conn, goal['id'], 'next-id', '')
        self.assertTrue(goals.finalize_transfer(self.conn, goal['id'])['finalized'])
        value = goals.get(self.conn, goal['id'])
        self.assertEqual((value['status'], value['manual_pause']), ('queued', False))
        self.assertEqual([item['wake'] for item in self.notices('next-id')], [1])

    def test_only_the_user_or_current_leader_can_hand_over_and_only_to_an_active_leader(self):
        goal = self.goal()
        for actor in ('member-id', 'next-id'):
            with self.subTest(actor=actor), self.assertRaises(board.BoardError):
                goals.request_transfer(self.conn, goal['id'], 'next-id', '', actor=actor)
        for target in ('chief-id', 'member-id', 'next-member', ''):
            with self.subTest(target=target), self.assertRaises(board.BoardError):
                goals.request_transfer(self.conn, goal['id'], target, '')
        goals.request_transfer(self.conn, goal['id'], 'next-id', '')
        board.join(self.conn, 'third-id', 'demo', 'third', self.root, self.root, 'main', role='leader')
        with self.assertRaises(board.Conflict):
            goals.request_transfer(self.conn, goal['id'], 'third-id', '')
        done = self.goal()
        goals.transition(self.conn, done['id'], 'running')
        self.accept(done['id'])
        with self.assertRaises(board.Conflict):
            goals.request_transfer(self.conn, done['id'], 'next-id', '')

    def test_receiver_that_left_before_the_turn_ended_fails_the_handover_and_keeps_the_owner(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='claude')
        goals.set_execution_status(self.conn, execution['id'], 'running')
        transfer = goals.request_transfer(self.conn, goal['id'], 'next-id', '', actor='chief-id')
        self.archive('next-id')
        goals.set_execution_status(self.conn, execution['id'], 'idle')
        self.assertFalse(goals.finalize_transfer(self.conn, goal['id'])['finalized'])
        self.assertEqual(goals.get_transfer(self.conn, transfer['id'])['status'], 'failed')
        self.assertEqual(goals.get(self.conn, goal['id'])['leader_session_id'], 'chief-id')
        self.assertTrue(goals.dispatch_check(self.conn, goal['id'])['allowed'])


class ControllerTransferTests(TransferFixture):
    def setUp(self):
        super().setUp()
        self.manager = FakeManager(self.home)
        self.controller = goal_runtime.Controller(self.manager)

    def test_host_waits_for_busy_turns_closes_idle_processes_and_hands_material_to_the_receiver(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='claude')
        goals.set_execution_status(self.conn, execution['id'], 'idle')
        self.manager.journal(execution['id']).events.extend([
            {'type': 'message', 'text': 'Measure the speaker handover'},
            {'type': 'assistant', 'content': [{'type': 'text', 'text': 'Sample 1 measured at 320ms.'}]}])
        goals.request_transfer(self.conn, goal['id'], 'next-id', 'Sample 2 still open.', actor='chief-id')
        self.manager.busy_keys.add(execution['id'])
        self.manager.running_keys.add(execution['id'])
        self.controller.sync(self.conn, goal['id'])
        self.assertIsNotNone(goals.pending_transfer(self.conn, goal['id']))
        self.assertEqual(self.manager.stopped, [])

        self.manager.busy_keys.clear()
        self.controller.sync(self.conn, goal['id'])
        self.assertIsNone(goals.pending_transfer(self.conn, goal['id']))
        self.assertEqual(self.manager.stopped, [execution['id']])
        self.assertEqual(goals.get(self.conn, goal['id'])['leader_session_id'], 'next-id')

        receiving = goals.create_execution(self.conn, goal['id'], 'next-id', runtime='claude')
        material = self.controller.transfer_handoff(self.conn, receiving)
        self.assertEqual(len(material), 1)
        self.assertIn('Sample 2 still open.', material[0]['text'])
        self.assertIn('Sample 1 measured at 320ms.', material[0]['text'])
        self.assertTrue(material[0]['protected'])
        self.assertEqual(self.controller.transfer_handoff(self.conn, execution), [])


class GoalTransferActionTests(TransferFixture):
    def setUp(self):
        super().setUp()
        self.host = Host(self.home)

    def dispatch(self, action, data, actor='chief-id', execution=None):
        return goal_actions.dispatch(self.home, actor, execution, action, data, self.host)

    def invoke_cli(self, args, environment=None):
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, environment or {}, clear=False), redirect_stdout(output), redirect_stderr(errors):
            code = cli.main(['--home', str(self.home), '--session', 'chief-id', *args], agents_provider=lambda: [], cwd=self.root)
        return code, output.getvalue(), errors.getvalue()

    def test_tool_and_cli_route_the_handover_through_the_shared_action(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='claude')
        goals.set_execution_status(self.conn, execution['id'], 'running')
        result = self.dispatch('transfer', {'goal_id': goal['id'], 'to': 'next', 'text': 'Notes'}, execution=execution['id'])
        self.assertTrue(result['pending_transfer'])
        self.assertIn('next', result['message'])
        self.assertIn(('sync', goal['id']), self.host.calls)
        with self.assertRaises(board.BoardError):
            self.dispatch('transfer', {'goal_id': goal['id'], 'to': 'next'}, actor='member-id')

        from crew import spawn
        with mock.patch.object(spawn, 'request_start', return_value={'ok': True}) as request:
            code, _, errors = self.invoke_cli(['goal', 'transfer', goal['id'], '--to', 'next', '--text', 'Notes'],
                                              {'CREW_SESSION_ID': 'chief-id'})
        self.assertEqual(code, 0, errors)
        body = request.call_args.args[-1]
        self.assertEqual((body['action'], body['data']['to'], body['data']['text'], body['data']['goal_id']),
                         ('transfer', 'next', 'Notes', goal['id']))


if __name__ == '__main__':
    import unittest
    unittest.main()
