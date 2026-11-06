"""A member's finished task turn reaches its leader even when the member forgot to report.

User decision 2026-11-06: when a member ends a turn inside a task execution
without sending a result, Crew hands that turn's last reply to the goal leader
as a result, marked as returned by Crew. Never twice for one turn, never when
the member already reported, never for the leader's own execution.
"""
import tests  # Install the production-service connection guard.
import json
import unittest
from contextlib import closing

from crew import board, chat, goal_messages, goals, store
from test_member_native_binding import MemberNativeFixture, wait_until


class MemberTurnResultTests(MemberNativeFixture, unittest.TestCase):
    def leader_mail(self, sid):
        return [dict(row) for row in self.conn.execute(
            "SELECT p.id,p.body,p.kind,m.source_execution,m.status FROM peer_messages p "
            "JOIN goal_message_meta m ON m.message_id=p.id WHERE p.sender_session_id=? AND p.target_session_id='s3' "
            'ORDER BY p.created_at', (sid,))]

    def test_claude_member_turn_without_result_is_returned_to_the_leader(self):
        goal, sid, key, _ = self.claude_goal_member()
        mail = wait_until(lambda: self.leader_mail(sid), 'returned result')
        self.assertEqual(len(mail), 1)
        self.assertEqual(mail[0]['kind'], 'result')
        self.assertEqual(mail[0]['source_execution'], key)
        self.assertEqual(mail[0]['status'], 'queued')
        self.assertIn('Crew 代为交回', mail[0]['body'])
        self.assertIn('不是组员主动回报', mail[0]['body'])
        self.assertIn('1 + 1 = 2.', mail[0]['body'])

        # Re-processing the same finished turn does not send it again.
        journal = self.manager.journal(key)
        with closing(store.connect(self.home)) as conn:
            goal_messages.return_unreported(conn, key, list(journal.events))
        self.assertEqual(len(self.leader_mail(sid)), 1)

        # The leader receives it through the normal task mail delivery.
        self.manager.deliver_messages()
        leader = self.manager.goal_controller.current(self.conn, goal['id'], 's3')
        delivered = [event for event in self.manager.journal(leader['id']).events if event['type'] == 'message']
        self.assertIn('1 + 1 = 2.', delivered[-1]['text'])

    def test_member_that_reported_in_the_turn_is_not_reported_again(self):
        goal = self.goal()
        created = self.manager.start(self.conn, 'demo', 'arith', 'Compute 1+1', runtime='claude',
                                     caller_session_id='s3', goal_id=goal['id'])
        sid, key = created['session_id'], created['execution_id']
        native = goals.get_execution(self.conn, key)['bindings'][0]['native_id']
        self.manager.send_message(sid, 's3', 'Answer: 2', execution_id=key, kind='result')
        self.finish_claude_turn(key, self.processes[0], native)
        self.assertEqual([item['body'] for item in self.leader_mail(sid)], ['Answer: 2'])

    def test_next_turn_without_report_is_returned_separately(self):
        goal, sid, key, native = self.claude_goal_member()
        wait_until(lambda: self.leader_mail(sid), 'first returned result')
        self.manager.goal_controller.send(goal['id'], sid, 'Now compute 2+2', source='peer', sender='lead')
        self.finish_claude_turn(key, self.processes[0], native, text='2 + 2 = 4.')
        mail = wait_until(lambda: len(self.leader_mail(sid)) == 2 and self.leader_mail(sid), 'second returned result')
        self.assertIn('2 + 2 = 4.', mail[1]['body'])

    def test_human_conversation_and_leader_execution_are_not_returned(self):
        goal, sid, key, native = self.claude_goal_member()
        wait_until(lambda: self.leader_mail(sid), 'first returned result')
        self.manager.goal_controller.send(goal['id'], sid, 'Why did you pick that method?', source='human')
        self.finish_claude_turn(key, self.processes[0], native, text='Because it is direct.')
        self.assertEqual(len(self.leader_mail(sid)), 1)

        self.manager.goal_controller.send(goal['id'], 's3', 'Plan the work', source='crew')
        leader = self.manager.goal_controller.current(self.conn, goal['id'], 's3')
        leader_native = leader['bindings'][0]['native_id']
        self.finish_claude_turn(leader['id'], self.processes[-1], leader_native, text='Plan ready.')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM peer_messages WHERE sender_session_id='s3'").fetchone()[0], 0)

    def test_failed_turn_is_not_returned_as_a_result(self):
        goal = self.goal()
        created = self.manager.start(self.conn, 'demo', 'arith', 'Compute 1+1', runtime='claude',
                                     caller_session_id='s3', goal_id=goal['id'])
        sid, key = created['session_id'], created['execution_id']
        process = self.processes[0]
        native = goals.get_execution(self.conn, key)['bindings'][0]['native_id']
        process.emit({'type': 'assistant', 'session_id': native,
                      'message': {'id': 'partial', 'content': [{'type': 'text', 'text': 'Halfway'}]}})
        process.emit({'type': 'result', 'subtype': 'error_during_execution', 'is_error': True,
                      'session_id': native, 'errors': ['API Error: overloaded']})
        wait_until(lambda: self.manager.journal(key).status == 'idle', 'failed turn end')
        self.assertEqual(self.leader_mail(sid), [])

    def test_codex_member_turn_without_result_is_returned_to_the_leader(self):
        goal = self.goal()
        created = self.manager.start(self.conn, 'demo', 'arith', 'Compute 1+1', runtime='codex',
                                     caller_session_id='s3', goal_id=goal['id'])
        sid, key = created['session_id'], created['execution_id']
        client = self.clients[0]
        client.emit('item/completed', turnId=client.turn_id,
                    item={'id': 'answer', 'type': 'agentMessage', 'text': '1 + 1 = 2 (Codex).'})
        client.finish()
        mail = wait_until(lambda: self.leader_mail(sid), 'codex returned result')
        self.assertEqual(len(mail), 1)
        self.assertEqual(mail[0]['source_execution'], key)
        self.assertIn('1 + 1 = 2 (Codex).', mail[0]['body'])
        self.assertIn('Crew 代为交回', mail[0]['body'])


if __name__ == '__main__':
    unittest.main()
