"""Workspace management through real loopback HTTP and isolated native fakes."""
import tests
import threading
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from crew import board, goals, store, web
import test_chat_http as chat_helpers
from test_shell import NOW, PLUGIN, ShellFixture


class WorkspaceSettingsHttpTests(ShellFixture, unittest.TestCase):
    request = chat_helpers.ChatHttpTests.request
    close_server = chat_helpers.ChatHttpTests.close_server
    start_leader = chat_helpers.ChatHttpTests.start_leader
    finish_turn = chat_helpers.ChatHttpTests.finish_turn

    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory(prefix='crew-workspace-http-')
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / 'Original Workspace'
        self.project.mkdir()
        self.conn.execute('UPDATE projects SET root=? WHERE name=?', (store.norm_path(self.project), 'demo'))
        self.conn.execute('UPDATE sessions SET cwd=?,worktree=NULL WHERE project=?', (store.norm_path(self.project), 'demo'))
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=lambda: [], claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = self.server.url
        self.destination = Path(temporary.name) / 'Relocated Workspace'
        self.destination.mkdir()

    def update(self, **changes):
        return self.request('/api/project/update?project=demo', {
            'project': 'demo', 'display_name': None, 'root': str(self.project), 'revision': 0, **changes})

    def test_snapshot_and_rename_preserve_stable_identity_and_live_process(self):
        sid, process = self.start_leader()
        code, snapshot, _ = self.request('/api/project/settings?project=demo')
        self.assertEqual(code, 200, snapshot)
        self.assertEqual(snapshot['revision'], 0)
        self.assertTrue(next(item for item in snapshot['sessions'] if item['session_id'] == sid)['running'])
        code, saved, _ = self.update(display_name='改名后的工作区')
        self.assertEqual(code, 200, saved)
        self.assertEqual(saved['project']['name'], 'demo')
        self.assertEqual(saved['project']['display_name'], '改名后的工作区')
        self.assertEqual(saved['revision'], 1)
        self.assertEqual(board.session(self.conn, sid)['project'], 'demo')
        self.assertIsNone(process.poll())
        code, listed, _ = self.request('/api/projects')
        self.assertEqual(listed['projects'][0]['display_name'], '改名后的工作区')

    def test_parent_directory_relink_stops_and_moves_all_session_cwds(self):
        sid, process = self.start_leader()
        parent = self.project.parent
        code, saved, _ = self.update(root=str(parent), stop=True)
        self.assertEqual(code, 200, saved)
        self.assertEqual(Path(saved['project']['root']).resolve(), parent.resolve())
        self.assertIsNotNone(process.poll())
        for session in board.sessions(self.conn, 'demo'):
            self.assertEqual(session['cwd'], str(parent.resolve()))
        self.assertEqual(board.session(self.conn, sid)['cwd'], str(parent.resolve()))

    def test_preflight_errors_do_not_stop_or_modify_any_session(self):
        sid, process = self.start_leader()
        stop = self.server.service.chat.stop_identity
        with mock.patch.object(self.server.service.chat, 'stop_identity', wraps=stop) as stopped:
            for changes, error in (
                ({'root': str(self.destination)}, '明确确认'),
                ({'root': str(self.destination / 'missing'), 'stop': True}, '目录'),
                ({'root': str(self.destination), 'display_name': 'x' * 101, 'stop': True}, '100'),
                ({'root': str(self.destination), 'revision': 99, 'stop': True}, '更改'),
            ):
                with self.subTest(changes=changes):
                    code, result, _ = self.update(**changes)
                    self.assertIn(code, (400, 409), result)
                    self.assertIn(error, result['error'])
            stopped.assert_not_called()
        self.assertIsNone(process.poll())
        self.assertEqual(store.norm_path(board.session(self.conn, sid)['cwd']), store.norm_path(self.project))
        self.assertEqual(store.norm_path(board.project(self.conn, 'demo')['root']), store.norm_path(self.project))

    def test_migration_stops_idle_native_pauses_queued_goal_and_resumes_in_new_directory(self):
        sid, process = self.start_leader()
        code, created, _ = self.request('/api/goals/create?project=demo', {
            'leader_session_id': sid, 'title': 'Keep this goal', 'request': 'Keep original requirements',
            'acceptance': ['A reproducible result']})
        self.assertEqual(code, 200, created)
        goal_id = created['goal']['id']
        native_count = len(self.processes)
        old_text = (self.server.service.chat.path(sid)).read_text(encoding='utf-8')
        code, saved, _ = self.update(root=str(self.destination), stop=True)
        self.assertEqual(code, 200, saved)
        self.assertEqual(Path(saved['project']['root']).resolve(), self.destination.resolve())
        self.assertIsNotNone(process.poll(), 'Idle but still-alive native must exit before paths change')
        self.assertEqual(len(self.processes), native_count, 'Migration must not wake or restart AI')
        self.assertEqual(goals.get(self.conn, goal_id)['status'], 'paused')
        self.assertTrue((self.server.service.chat.path(sid)).read_text(encoding='utf-8').startswith(old_text))
        for row in self.conn.execute('SELECT cwd FROM sessions WHERE project=?', ('demo',)):
            self.assertEqual(Path(row['cwd']).resolve(), self.destination.resolve())
        code, sent, _ = self.request('/api/chat/send?project=demo', {'session_id': sid, 'text': 'Continue in the selected folder'})
        self.assertEqual(code, 200, sent)
        self.assertEqual(Path(self.processes[-1].options['cwd']).resolve(), self.destination.resolve())

    def test_failed_native_stop_retains_root_and_reports_the_actual_reason(self):
        with mock.patch.object(self.server.service.chat, 'stop_identity', side_effect=board.Conflict('外部会话仍在运行')):
            code, result, _ = self.update(root=str(self.destination), stop=True)
        self.assertEqual(code, 409, result)
        self.assertIn('外部会话仍在运行', result['error'])
        self.assertEqual(store.norm_path(board.project(self.conn, 'demo')['root']), store.norm_path(self.project))

    def test_http_document_ids_read_old_and_relocated_files_without_granting_old_path_scope(self):
        sid, process = self.start_leader()
        old_folder = self.project / 'docs'
        old_folder.mkdir()
        entries = []
        for name, body in (('retained.md', '# Remains in original folder'),
                           ('relocated.md', '# Relocated original document')):
            path = old_folder / name
            path.write_text(body, encoding='utf-8')
            code, document, _ = self.request('/api/documents?project=demo', {
                'session_id': sid, 'path': str(path), 'title': name, 'summary': 'Original metadata'})
            self.assertEqual(code, 200, document)
            entries.append((path, body, document))
        moved = self.destination / 'docs' / 'relocated.md'
        moved.parent.mkdir()
        entries[1][0].rename(moved)
        process_count = len(self.processes)
        code, saved, _ = self.update(root=str(self.destination), stop=True)
        self.assertEqual(code, 200, saved)
        self.assertIsNotNone(process.poll())
        code, listed, _ = self.request('/api/documents?project=demo')
        self.assertEqual(code, 200, listed)
        documents = {item['id']: item for item in listed['documents']}
        for (old_path, body, original), expected in zip(entries, (entries[0][0], moved)):
            with self.subTest(document=original['title']):
                card = documents[original['id']]
                self.assertEqual((card['path'], card['title'], card['summary'], card['session_id']),
                                 (original['path'], original['title'], original['summary'], sid))
                self.assertTrue(card['exists'])
                self.assertEqual(card['resolved_path'], str(expected.resolve()))
                code, content, _ = self.request('/api/files/content?project=demo&document_id=' + str(original['id']))
                self.assertEqual(code, 200, content)
                self.assertEqual((content['path'], content['content']), (str(expected.resolve()), body))
                code, refused, _ = self.request('/api/files/resolve?project=demo', {'session_id': sid, 'path': str(old_path)})
                self.assertEqual(code, 400, refused)
                self.assertIn('不在当前项目', refused['error'])
        other = self.destination.parent / 'Other Workspace'
        other.mkdir()
        code, initialized, _ = self.request('/api/project/init', {'name': 'other', 'root': str(other)})
        self.assertEqual(code, 200, initialized)
        code, refused, _ = self.request('/api/files/content?project=other&document_id=' + str(entries[0][2]['id']))
        self.assertEqual(code, 400, refused)
        self.assertIn('不属于当前工作区', refused['error'])
        self.assertEqual(len(self.processes), process_count, 'Document reads and migration must not wake AI')

    def test_removal_stops_execution_retains_files_and_records_and_explicit_reopen_restores(self):
        sid, process = self.start_leader()
        keep = self.project / 'keep.txt'
        keep.write_text('User data stays here', encoding='utf-8')
        identities = [row[0] for row in self.conn.execute('SELECT session_id FROM sessions WHERE project=? ORDER BY session_id', ('demo',))]
        code, removed, _ = self.request('/api/project/remove?project=demo', {'project': 'demo', 'revision': 0, 'stop': True})
        self.assertEqual(code, 200, removed)
        self.assertTrue(removed['removed'])
        self.assertIsNotNone(process.poll())
        self.assertEqual(self.request('/api/projects')[1]['projects'], [])
        self.assertEqual(keep.read_text(encoding='utf-8'), 'User data stays here')
        self.assertEqual([row[0] for row in self.conn.execute('SELECT session_id FROM sessions WHERE project=? ORDER BY session_id', ('demo',))], identities)
        count = len(self.processes)
        code, rejected, _ = self.request('/api/chat/send?project=demo', {'session_id': sid, 'text': 'Do not launch'})
        self.assertEqual(code, 409, rejected)
        self.assertEqual(len(self.processes), count)
        code, restored, _ = self.request('/api/project/init', {'name': 'folder-basename', 'root': str(self.project)})
        self.assertEqual(code, 200, restored)
        self.assertEqual(restored['name'], 'demo')
        self.assertEqual(self.request('/api/project/settings?project=demo')[1]['revision'], 2)
        self.assertEqual(len(self.processes), count)

    def test_project_query_and_body_must_match(self):
        code, result, _ = self.update(project='other')
        self.assertEqual(code, 400, result)
        self.assertIn('不一致', result['error'])


if __name__ == '__main__':
    unittest.main()
