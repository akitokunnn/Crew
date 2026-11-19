"""A member is the execution of one part of a task: once its tasks end it is archived."""
import tests
import unittest

from crew import board, goals
from test_shell import NOW, ShellFixture


class MemberRetireTests(ShellFixture, unittest.TestCase):
    def member_on(self, name, status):
        board.join(self.conn, name + '-id', 'demo', name, self.project, self.project, 'main', now=NOW)
        board.roster(self.conn, 'demo', 'move', 'lead', [name], now=NOW)
        goal = goals.create(self.conn, 'demo', 's3', 'Task for ' + name, 'Request', ['Done'])
        goals.assign(self.conn, goal['id'], name + '-id', 'One part')
        self.conn.execute('UPDATE goals SET status=? WHERE id=?', (status, goal['id']))
        return name + '-id', goal

    def archived(self, session_id):
        return board.is_archived(self.conn, session_id)

    def test_members_of_ended_tasks_are_archived_and_others_stay(self):
        done, _ = self.member_on('done', 'completed')
        cancelled, _ = self.member_on('cancelled', 'cancelled')
        running, _ = self.member_on('running', 'running')
        failed, _ = self.member_on('failed', 'failed')
        review, _ = self.member_on('review', 'awaiting_acceptance')
        retired = self.manager.goal_controller.retire_finished_members(self.conn)
        self.assertCountEqual(retired, [done, cancelled])
        self.assertTrue(self.archived(done) and self.archived(cancelled))
        for kept in (running, failed, review, 's1'):
            self.assertFalse(self.archived(kept), kept)
        self.assertEqual(board.session(self.conn, done)['status'], 'closed')
        self.assertEqual(self.manager.goal_controller.retire_finished_members(self.conn), [])

    def test_a_member_with_another_open_task_stays_until_that_one_ends(self):
        member, _ = self.member_on('busy', 'completed')
        second = goals.create(self.conn, 'demo', 's3', 'Second', 'Request', ['Done'])
        goals.assign(self.conn, second['id'], member, 'Another part')
        self.assertEqual(self.manager.goal_controller.retire_finished_members(self.conn), [])
        self.conn.execute("UPDATE goals SET status='completed' WHERE id=?", (second['id'],))
        self.assertEqual(self.manager.goal_controller.retire_finished_members(self.conn), [member])

    def test_handover_releases_the_part_and_retires_its_member(self):
        member, goal = self.member_on('handed', 'running')
        self.conn.execute("UPDATE goal_assignments SET status='released' WHERE goal_id=? AND session_id=?",
                          (goal['id'], member))
        self.assertEqual(self.manager.goal_controller.retire_finished_members(self.conn), [member])

    def test_sync_retires_members_of_ended_tasks(self):
        member, _ = self.member_on('synced', 'completed')
        self.manager.goal_controller.sync(self.conn)
        self.assertTrue(self.archived(member))


if __name__ == '__main__':
    unittest.main()
