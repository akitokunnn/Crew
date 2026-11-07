"""A task whose leader ended its turn without submitting ``finish`` gets one reminder.

User report 2026-11-07: tasks sometimes stayed "running" although the leader had
already told the user the work was ready for acceptance, so the user never saw
the accept / request-changes buttons. When the leader's own task turn ends with
nothing else in flight, Crew reminds that execution once per ended turn; a turn
that was itself the reminder is never reminded again.
"""
import tests  # Install the production-service connection guard.
import unittest
from datetime import datetime, timedelta, timezone

from crew import goal_messages, goals, runtimes
from test_member_native_binding import MemberNativeFixture, wait_until


def later(event, seconds):
    at = datetime.fromisoformat(event['at'].replace('Z', '+00:00')) + timedelta(seconds=seconds)
    return at.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


class LeaderFinishReminderTests(MemberNativeFixture, unittest.TestCase):
    def leader_turn(self, goal, text='已完成，等你验收。'):
        controller = self.manager.goal_controller
        controller.send(goal['id'], 's3', 'Plan and deliver the work', source='crew')
        leader = controller.current(self.conn, goal['id'], 's3')
        self.finish_claude_turn(leader['id'], self.processes[-1], leader['bindings'][0]['native_id'], text=text)
        controller.sync(self.conn, goal['id'])
        return leader['id']

    def reminders(self, key):
        return [event for event in self.manager.journal(key).events
                if event['type'] == 'message' and event['id'].startswith(goal_messages.FINISH_REMINDER)]

    def remind(self, key, seconds):
        end = [event for event in self.manager.journal(key).events if event['type'] == 'turn_result'][-1]
        goal_messages.remind_unfinished(self.manager.goal_controller, self.conn, now=later(end, seconds))

    def test_leader_that_only_announced_delivery_is_reminded_once(self):
        goal = self.goal()
        key = self.leader_turn(goal)
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'running')
        self.remind(key, 5)
        self.assertEqual(self.reminders(key), [], 'a just-ended turn gets a grace period first')
        self.remind(key, 60)
        sent = self.reminders(key)
        self.assertEqual(len(sent), 1)
        self.assertIn('收尾检查', sent[0]['text'])
        self.assertIn('crew goal finish', sent[0]['text'])
        self.assertEqual(sent[0]['source'], 'crew')
        self.remind(key, 120)
        self.assertEqual(len(self.reminders(key)), 1, 'the pending reminder is not sent twice')

        leader = goals.get_execution(self.conn, key)
        self.finish_claude_turn(key, self.processes[-1], leader['bindings'][0]['native_id'], text='在等 Git 助手的回执。')
        self.manager.goal_controller.sync(self.conn, goal['id'])
        self.remind(key, 600)
        self.assertEqual(len(self.reminders(key)), 1, 'a turn that answered the reminder is never reminded again')

    def test_codex_leader_is_reminded_the_same_way(self):
        runtimes.set(self.conn, 's3', 'codex', 'general-native')
        goal = self.goal()
        controller = self.manager.goal_controller
        controller.send(goal['id'], 's3', 'Plan and deliver the work', source='crew')
        key = controller.current(self.conn, goal['id'], 's3')['id']
        client = self.clients[-1]
        client.emit('item/completed', turnId=client.turn_id, item={'id': 'done', 'type': 'agentMessage', 'text': '已完成，等你验收。'})
        client.finish()
        wait_until(lambda: self.manager.journal(key).status == 'idle', 'codex leader turn end')
        controller.sync(self.conn, goal['id'])
        self.remind(key, 60)
        sent = self.reminders(key)
        self.assertEqual(len(sent), 1)
        self.assertIn('收尾检查', sent[0]['text'])

    def test_busy_member_or_submitted_finish_means_no_reminder(self):
        goal = self.goal()
        key = self.leader_turn(goal)
        created = self.manager.start(self.conn, 'demo', 'arith', 'Compute 1+1', runtime='claude',
                                     caller_session_id='s3', goal_id=goal['id'])
        wait_until(lambda: self.manager.busy(created['execution_id']), 'member working')
        self.manager.goal_controller.sync(self.conn, goal['id'])
        self.remind(key, 60)
        self.assertEqual(self.reminders(key), [], 'a member is still working on the task')

    def test_pending_finish_means_no_reminder(self):
        goal = self.goal()
        key = self.leader_turn(goal)
        self.conn.execute('PRAGMA foreign_keys=OFF')  # Only the pending-finish row matters here, not a real report.
        self.conn.execute("INSERT INTO goal_round_finishes(goal_id,round,requirement_version,report_id,outcome,status,actor,"
                          "execution_id,requested_at) VALUES(?,?,?,?,?,?,?,?,?)",
                          (goal['id'], 1, 1, 'report', 'awaiting_acceptance', 'pending', 's3', key, '2026-11-07T00:00:00.000Z'))
        self.remind(key, 60)
        self.assertEqual(self.reminders(key), [])


if __name__ == '__main__':
    unittest.main()
