"""Producers of the state that lives outside the database (docs/EVENT_STREAM_DESIGN.md §6.2).

Managed state (conversation journals, owned processes, scans, connections, the digest manager) marks a
`change_feed.Notifier`; the service's change worker records the marks, resolving a conversation key to its
workspace; a reader builds the current value with the same functions the snapshot uses.
"""
import tests
import json
import sqlite3
import unittest
from unittest import mock

from crew import board, change_feed, goals, store, web
from crew.web_service.snapshot import CONVERSATION_FIELDS
from test_shell import NOW, PLUGIN, ShellFixture


def plain(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


class ManagedFeedTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.agents = []
        self.service = web.Service(self.home, PLUGIN, agents_provider=lambda: list(self.agents), claude_root=self.claude,
                                   codex_root=self.codex, runs_file=self.runs, process_factory=self.process_factory,
                                   isolated_sources=True)
        self.addCleanup(self.service.chat.close)
        self.service.chat.changes.take()
        self.seq = change_feed.latest_seq(self.conn)

    def recorded(self):
        """Rows the change worker records for what was marked since the last call."""
        self.service._record_changes()
        rows = [(row['scope'], row['type'], row['entity']) for row in
                self.conn.execute('SELECT scope,type,entity FROM change_log WHERE seq>? ORDER BY seq', (self.seq,))]
        self.seq = change_feed.latest_seq(self.conn)
        return rows

    def read(self, after, viewer='demo'):
        return change_feed.read(self.conn, change_feed.scopes(viewer), after,
                                lambda conn, kind, project, entity: self.service.change_value(conn, kind, project, entity, viewer))

    def test_a_journal_change_invalidates_its_workspace_cards_and_the_sounds(self):
        before = self.seq
        self.service.chat.journal('s3').emit('message', id='m1', role='user', text='你好', source='human')
        self.assertEqual(self.recorded(), [('workspace:demo', 'conversations.invalidated', 'demo'),
                                           ('global', 'sound_events.invalidated', 'sound_events')])
        state = plain(self.service.state('demo'))
        cards, sounds = self.read(before)
        self.assertEqual(cards['data'], {key: state[key] for key in CONVERSATION_FIELDS})
        self.assertEqual(sounds['data'], {'events': state['sounds']['events']})
        [leader] = cards['data']['leaders']
        self.assertEqual((leader['session_id'], leader['last_seq'], leader['web_chat']), ('s3', 1, True))

    def test_status_and_streamed_progress_mark_even_without_a_new_event(self):
        journal = self.service.chat.journal('s1')
        self.service.chat.changes.take()
        journal.set_status('offline')
        self.assertEqual(self.service.chat.changes.take()[1], {'s1'})
        journal.consume({'type': 'stream_event', 'event': {'type': 'message_delta', 'usage': {'output_tokens': 3}}})
        self.assertEqual(self.service.chat.changes.take()[1], {'s1'})

    def test_a_task_execution_key_finds_the_workspace_of_its_task(self):
        goal = goals.create(self.conn, 'demo', 's3', '数据层', '搭数据层', ['可复核'])
        execution = goals.create_execution(self.conn, goal['id'], 's3', runtime='claude')
        self.recorded()
        self.service.chat.journal(execution['id']).emit('status', status='working')
        self.assertIn(('workspace:demo', 'conversations.invalidated', 'demo'), self.recorded())

    def test_a_conversation_outside_every_workspace_only_sounds(self):
        self.service.chat.changes.session('assistant-without-workspace')
        self.assertEqual(self.recorded(), [('global', 'sound_events.invalidated', 'sound_events')])

    def test_the_process_scan_marks_every_workspace_only_when_its_result_changes(self):
        self.service.refresh_agents()
        self.assertEqual(self.service.chat.changes.take(), ([('scanner', None, 'scanner')], set(), {'conversations'}))
        self.service.refresh_agents()
        self.assertEqual(self.service.chat.changes.take(), ([], set(), set()))
        self.agents.append({'sessionId': 's1', 'name': 'cc-one', 'pid': 7, 'status': 'busy', 'cwd': str(self.project)})
        self.service.refresh_agents()
        self.assertEqual(self.service.chat.changes.take(), ([], set(), {'conversations'}))
        (self.root / 'other').mkdir()
        board.init_project(self.conn, 'other', self.root / 'other')
        self.service.chat.changes.everywhere('conversations')
        rows = self.recorded()
        self.assertIn(('workspace:demo', 'conversations.invalidated', 'demo'), rows)
        self.assertIn(('workspace:other', 'conversations.invalidated', 'other'), rows)

    def test_new_dispatch_receipts_invalidate_the_cards_with_the_scan(self):
        self.service.scan_usage()
        seq = change_feed.latest_seq(self.conn)
        self.write(self.runs, [self.run_record('receipt-1', cwd=str(self.project))])
        self.service.scan_usage()
        types = [row['type'] for row in self.conn.execute('SELECT type FROM change_log WHERE seq>?', (seq,))]
        self.assertIn('conversations.invalidated', types)
        seq = change_feed.latest_seq(self.conn)
        self.service.scan_usage()
        self.assertNotIn('conversations.invalidated',
                         [row['type'] for row in self.conn.execute('SELECT type FROM change_log WHERE seq>?', (seq,))])

    def test_a_channel_field_set_to_a_new_value_marks_the_notification_field_once(self):
        inbox = self.service.connections.inboxes['qq']
        inbox.error = inbox.error
        self.assertEqual(self.service.chat.changes.take()[0], [])
        inbox.error = '连接失败'
        self.assertEqual(self.service.chat.changes.take()[0], [('notification', None, 'notification')])
        before = self.seq
        self.service.chat.changes.mark('notification', None, 'notification')
        self.recorded()
        [change] = self.read(before)
        self.assertEqual(change['data'], plain(self.service.state('demo'))['notification'])

    def test_global_managed_values_match_the_snapshot_for_the_entry_page_and_a_workspace(self):
        for kind in ('runtimes', 'scanner', 'sound_events'):
            self.service.chat.changes.mark(kind, None, kind)
        self.service.chat.changes.mark('channel_digest', 'demo', 'demo')
        before = self.seq
        self.recorded()
        for viewer in (None, 'demo'):
            state = plain(self.service.state(viewer))
            found = {change['type']: change['data'] for change in self.read(before, viewer)}
            self.assertEqual(found['runtimes.updated'], state['runtimes'])
            self.assertEqual(found['scanner.updated'], {key: state[key] for key in ('agents_error', 'vscode_error')})
            self.assertEqual(found['sound_events.updated'], {'events': state['sounds']['events']})
            if viewer is None:
                self.assertNotIn('channel_digest.updated', found)
            else:
                self.assertEqual(found['channel_digest.updated'],
                                 {key: state['channel'][key] for key in ('digesting', 'digest_error')})

    def test_marks_survive_a_failed_recording_and_the_last_ones_are_recorded_on_close(self):
        self.service.chat.changes.mark('runtimes', None, 'runtimes')
        with mock.patch('crew.store.connect', side_effect=sqlite3.OperationalError('database is locked')), \
                mock.patch('traceback.print_exc'):
            self.service._record_changes()
        self.assertEqual(self.service.chat.changes.take()[0], [('runtimes', None, 'runtimes')])
        self.service.chat.changes.mark('runtimes', None, 'runtimes')
        self.service.start()
        self.service.close()
        self.assertIn(('global', 'runtimes.invalidated', 'runtimes'), self.recorded())


if __name__ == '__main__':
    unittest.main()
