import tests
import unittest
from unittest import mock

import test_vscode_service as fixtures
from test_vscode_service import identity, NOW
from crew import board, goals, session_ops, workspace_activity


class WorkspaceActivityTests(unittest.TestCase):
    setUp = fixtures.VSCodeServiceTests.setUp
    hidden = fixtures.VSCodeServiceTests.hidden
    register = fixtures.VSCodeServiceTests.register

    def snapshot(self):
        return workspace_activity.snapshot(self.service, self.conn, 'alpha')

    def test_reading_status_does_not_scan_launch_or_mark_messages_read(self):
        self.register(identity(1), 'leader', role='leader')
        self.service.chat.journal(identity(1)).emit('assistant', id='reply', text='A saved reply')
        before = self.conn.total_changes
        with mock.patch.object(self.service, 'scan_usage', side_effect=AssertionError('No scan')):
            value = self.snapshot()
        self.assertEqual(value['counts']['unread'], 1)
        self.assertEqual(self.conn.total_changes, before)
        self.assertEqual(board.read_seq(self.conn, identity(1)), 0)
        self.service.chat.process_factory.assert_not_called()

    def test_working_is_observed_not_inferred_from_stale_database_status(self):
        self.register(identity(1), 'leader', role='leader')
        board.set_status(self.conn, identity(1), 'working', now=NOW)
        self.assertEqual(self.snapshot()['counts']['working'], 0)
        with mock.patch.object(self.service.chat, 'busy', side_effect=lambda key: key == identity(1)):
            value = self.snapshot()
        self.assertEqual(value['counts']['working'], 1)
        self.assertEqual(next(item for item in value['items'] if item['kind'] == 'working')['name'], 'leader')

    def test_held_and_archived_questions_do_not_become_tab_alerts(self):
        self.register(identity(1), 'leader', role='leader')
        self.register(identity(2), 'old', role='leader')
        active = board.ask(self.conn, identity(1), 'Choose an option', ['a', 'b'], now=NOW)
        held = board.ask(self.conn, identity(1), 'Later', ['a', 'b'], now=NOW)
        board.ask(self.conn, identity(2), 'Old question', ['a', 'b'], now=NOW)
        board.hold(self.conn, held, now=NOW)
        session_ops.archive(self.conn, identity(2), now=NOW)
        value = self.snapshot()
        self.assertEqual(value['counts']['decisions'], 1)
        self.assertEqual([item['title'] for item in value['items'] if item['kind'] == 'decision'], ['Choose an option'])

    def test_current_acceptance_and_saved_report_are_project_facts(self):
        self.register(identity(1), 'leader', role='leader')
        goal = goals.create(self.conn, 'alpha', identity(1), 'A delivered feature', 'Request', ['Works'])
        self.conn.execute("UPDATE goals SET status='awaiting_acceptance' WHERE id=?", (goal['id'],))
        self.conn.execute('INSERT INTO goal_reports(id,goal_id,round,requirement_version,version,outcome,data_json,html,created_at) '
                          'VALUES(?,?,?,?,?,?,?,?,?)', ('r', goal['id'], 1, 1, 1, 'awaiting_acceptance', '{"summary":"Saved evidence"}', '', NOW)); self.conn.commit()
        value = self.snapshot()
        self.assertEqual(value['counts']['acceptance'], 1)
        self.assertEqual(next(item['detail'] for item in value['items'] if item['kind'] == 'delivery'), 'Saved evidence')

    def test_partial_process_discovery_does_not_claim_the_project_is_idle(self):
        self.assertTrue(self.snapshot()['unknown'])
        self.service.agents_error = None
        self.assertFalse(self.snapshot()['unknown'])

    def test_internal_member_output_does_not_create_human_unread_noise(self):
        self.register(identity(1), 'leader', role='leader')
        self.register(identity(2), 'member', group_name='leader')
        self.service.chat.journal(identity(2)).emit('assistant', id='internal', text='Internal implementation output')
        self.assertEqual(self.snapshot()['counts']['unread'], 0)
