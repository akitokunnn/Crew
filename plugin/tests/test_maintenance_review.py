"""Independent product invariants, using only an isolated HTTP test fixture."""
import time
import json
import unittest
import tests
import test_context_http as context_helpers
from test_shell import NOW
from crew import board, goals, maintenance


class CleanupReviewTests(unittest.TestCase):
    def setUp(self):
        self.case = context_helpers.ContextHttpTests('test_state_leader_and_member_transcript_sources_and_null_unknown')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def clean(self):
        c = self.case
        code, preview = c.request('/api/maintenance/cleanup/preview?project=demo', {'project':'demo'})
        self.assertEqual(code, 200, preview)
        code, started = c.request('/api/maintenance/cleanup?project=demo',
            {'project':'demo', 'preview_id':preview['preview_id'], 'confirm':True})
        self.assertEqual(code, 200, started)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            value = maintenance.job(c.conn, 'demo', started['id'])
            if value['status'] != 'running':
                self.assertEqual(value['status'], 'completed', value)
                return value
            time.sleep(.01)
        self.fail('cleanup did not finish')

    def test_old_department_note_does_not_survive_as_active_note(self):
        c = self.case
        c.conn.execute("UPDATE groups SET note='旧部门工作习惯' WHERE project='demo' AND name='lead'")
        value = self.clean()
        self.assertIsNone(c.conn.execute("SELECT note FROM groups WHERE project='demo' AND name='lead'").fetchone()[0])
        self.assertEqual(next(group['note'] for group in value['detail']['archived_groups'] if group['name'] == 'lead'), '旧部门工作习惯')

    def test_cleanup_keeps_original_todo_note(self):
        c = self.case
        identity = c.conn.execute('INSERT INTO todos(project,session_id,title,steps,verification,note,created_at,updated_at) '
            'VALUES(?,?,?,?,?,?,?,?)', ('demo','s3','需要人工确认','确认输入','看到结果','原始用户备注',NOW,NOW)).lastrowid
        self.clean()
        value = c.conn.execute('SELECT status,note FROM todos WHERE id=?', (identity,)).fetchone()
        self.assertEqual(value['status'], 'cancelled')
        self.assertIn('原始用户备注', value['note'])

    def test_cleanup_discards_old_summary_error_state_only_for_this_workspace(self):
        digest = self.case.server.service.channel_digest
        digest.errors.update(demo='旧整理错误', other='另一个项目的错误')
        digest.failed_upto['demo'] = 123
        self.clean()
        with self.case.chat.lock:
            self.assertNotIn('demo', digest.errors)
            self.assertNotIn('demo', digest.failed_upto)
            self.assertEqual(digest.errors['other'], '另一个项目的错误')

    def test_result_counts_include_people_archived_with_their_department(self):
        c = self.case
        expected = c.conn.execute("SELECT COUNT(*) FROM sessions WHERE project='demo' AND status<>'closed'").fetchone()[0]
        result = self.clean()
        self.assertEqual(result['detail']['result']['identities'], expected)

    def test_existing_live_process_is_held_against_new_business_input(self):
        c = self.case
        sid, process = c.start_session()
        c.finish_turn(sid, process)
        current = c.chat.processes[sid]
        current.maintenance = {'job':'review', 'position':0, 'owned':False}
        try:
            with self.assertRaises(board.Conflict):
                c.chat._send(sid, '这是新的普通业务输入')
        finally:
            current.maintenance = None

    def test_boundary_and_job_result_share_one_commit(self):
        c = self.case
        manager = c.server.service.maintenance
        preview = manager.cleanup_preview('demo')
        saved = manager._preview('cleanup', 'demo', preview['preview_id'])
        identity, _ = manager._create(c.conn, 'demo', 'workspace_cleanup', preview['preview_id'], saved['items'], {})
        c.conn.execute("UPDATE maintenance_items SET status='completed' WHERE job_id=? AND target<>'archive'", (identity,))
        with board.transaction(c.conn):
            manager._archive(c.conn, 'demo', identity)
            value = maintenance.job(c.conn, 'demo', identity)
            self.assertEqual(value['status'], 'completed')
            self.assertIn('boundary', value['detail'])

    def test_malformed_context_selection_returns_input_error(self):
        c = self.case
        code, preview = c.request('/api/maintenance/department/preview?project=demo',
            {'project':'demo', 'leader_session_id':'s3'})
        self.assertEqual(code, 200, preview)
        code, response = c.request('/api/maintenance/department/compact?project=demo',
            {'project':'demo', 'leader_session_id':'s3', 'preview_id':preview['preview_id'], 'targets':[[]]})
        self.assertEqual(code, 400, response)

    def test_finished_history_without_native_id_is_not_compactable(self):
        c = self.case
        sid, process = c.start_session()
        c.finish_turn(sid, process)
        c.stop_process(sid, process)
        value = goals.create(c.conn, 'demo', sid, '旧目标', '验证结果', ['可验证'])
        goals.transition(c.conn, value['id'], 'running')
        execution = goals.create_execution(c.conn, value['id'], sid, runtime='claude')
        goals.bind_native(c.conn, execution['id'], 'claude', 'review-native')
        c.conn.execute("UPDATE goal_executions SET status='completed' WHERE id=?", (execution['id'],))
        c.conn.execute("UPDATE goals SET status='completed' WHERE id=?", (value['id'],))
        c.conn.execute("UPDATE goal_rounds SET status='completed' WHERE goal_id=?", (value['id'],))
        c.conn.execute('UPDATE goal_execution_bindings SET native_id=NULL WHERE execution_id=?', (execution['id'],))
        preview = c.server.service.maintenance.department_preview('demo', sid)
        self.assertFalse(any(entry['available'] for person in preview['people'] for entry in person['contexts']
                             if entry['target'] == execution['id']))

    def test_preview_cannot_be_reused_for_another_department(self):
        c = self.case
        sid, process = c.start_session()
        c.finish_turn(sid, process)
        manager = c.server.service.maintenance
        preview = manager.department_preview('demo', sid)
        manager.department_start('demo', sid, preview['preview_id'], [sid])
        with self.assertRaises(board.Conflict):
            manager.department_start('demo', 's3', preview['preview_id'], [sid])

    def test_idle_department_also_compacts_finished_goal_history(self):
        c = self.case
        sid, process = c.start_session()
        c.finish_turn(sid, process)
        original = c.chat.processes[sid]
        goal = goals.create(c.conn, 'demo', sid, '完成的旧任务', '保留结果', ['原有成果'])
        goals.transition(c.conn, goal['id'], 'running')
        execution = goals.create_execution(c.conn, goal['id'], sid, runtime='claude')
        goals.bind_native(c.conn, execution['id'], 'claude', 'review-finished-native')
        c.conn.execute("UPDATE goal_executions SET status='completed' WHERE id=?", (execution['id'],))
        c.conn.execute("UPDATE goals SET status='completed' WHERE id=?", (goal['id'],))
        c.conn.execute("UPDATE goal_rounds SET status='completed' WHERE goal_id=?", (goal['id'],))

        def auto_compact(p):
            write = p.stdin.write
            def accept(text):
                count = write(text)
                for line in text.splitlines():
                    record = json.loads(line)
                    if record.get('type') == 'user' and record['message']['content'] == '/compact':
                        p.emit({'type':'system', 'subtype':'compact_boundary',
                                'compact_metadata':{'pre_tokens':10000, 'post_tokens':1000}})
                        p.emit({'type':'result', 'is_error':False})
                return count
            p.stdin.write = accept
            return p
        auto_compact(process)
        factory = c.chat.process_factory
        c.chat.process_factory = lambda command, **options: auto_compact(factory(command, **options))
        manager = c.server.service.maintenance
        preview = manager.department_preview('demo', sid)
        value = manager.department_start('demo', sid, preview['preview_id'], [sid, execution['id']])
        deadline = time.monotonic() + 6
        while value['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(.01)
            value = maintenance.job(c.conn, 'demo', value['id'])
        self.assertEqual(value['status'], 'completed', value)
        self.assertEqual(value['counts']['completed'], 2)
        self.assertIs(c.chat.processes[sid], original)
        self.assertIsNone(process.poll())
        self.assertEqual(goals.get(c.conn, goal['id'])['status'], 'completed')
        self.assertEqual(goals.get_execution(c.conn, execution['id'])['status'], 'completed')


if __name__ == '__main__':
    unittest.main(verbosity=2)
