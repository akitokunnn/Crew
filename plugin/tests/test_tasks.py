"""Task identity, legacy migration, authorization and atomic API retries."""
from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest

import tests
from crew import board, goals, store, tasks, trust_mode


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.project = self.root / 'project'
        self.other = self.root / 'other'
        self.project.mkdir()
        self.other.mkdir()
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)
        board.init_project(self.conn, 'demo', self.project)
        board.init_project(self.conn, 'other', self.other)

    def make_task(self, **kwargs):
        return tasks.create(self.conn, 'demo', '讨论可测的目标', **kwargs)

    def test_plain_folder_discussion_creates_one_stable_leader_without_git_or_execution(self):
        task = self.make_task(projects=['other'])
        self.assertEqual(task['state'], 'discussion')
        self.assertFalse(task['authorized'])
        self.assertFalse((self.project / '.git').exists())
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_executions').fetchone()[0], 0)
        self.assertEqual(board.session(self.conn, task['leader_session_id'])['role'], 'leader')
        self.assertEqual(tasks.list_tasks(self.conn, 'other')[0]['id'], task['id'])
        self.assertEqual(tasks.get(self.conn, task['id'], 'demo')['id'], tasks.get(self.conn, task['id'], 'other')['id'])

    def test_atomic_create_replay_and_request_reuse_rejection(self):
        data = {'request_id': 'create-once', 'text': '讨论'}
        create = lambda: {'task': self.make_task()}
        first = tasks.mutate(self.conn, 'create', data, create)
        self.assertEqual(first, tasks.mutate(self.conn, 'create', data, create))
        self.assertEqual(len(tasks.list_tasks(self.conn)), 1)
        with self.assertRaises(board.Conflict):
            tasks.mutate(self.conn, 'create', {**data, 'text': '其他内容'}, create)

    def test_concurrent_edits_preserve_exact_user_requirement_and_original(self):
        task = self.make_task()
        changed = tasks.update(self.conn, task['id'], task['version'], request='用户补充：保留这个精确条件')
        with self.assertRaises(board.Conflict):
            tasks.update(self.conn, task['id'], task['version'], request='过时AI覆盖')
        self.assertIn('用户补充：保留这个精确条件', changed['request'])
        self.assertNotIn('过时AI覆盖', tasks.get(self.conn, task['id'])['request'])
        self.assertEqual(goals._row(self.conn, task['id'])['original_request'], '讨论可测的目标')

    def test_mode_is_task_scoped_and_does_not_authorize_discussion(self):
        first, other = self.make_task(), self.make_task()
        changed = tasks.set_mode(self.conn, first['id'], first['version'], 'automatic')
        self.assertEqual(changed['mode'], 'automatic')
        self.assertFalse(changed['authorized'])
        self.assertEqual(tasks.get(self.conn, other['id'])['mode'], 'manual')
        with closing(store.connect(self.home)) as reopened:
            self.assertEqual(tasks.get(reopened, first['id'])['mode'], 'automatic')

    def execution(self, task):
        value = tasks.authorize(self.conn, task['id'], task['version'])
        goals.transition(self.conn, task['id'], 'running')
        return goals.create_execution(self.conn, task['id'], task['leader_session_id'], runtime='claude')

    def test_two_tasks_with_one_leader_enforce_distinct_decision_modes(self):
        first = self.make_task()
        other = self.make_task(leader_session_id=first['leader_session_id'])
        one, two = self.execution(first), self.execution(other)
        value = tasks.get(self.conn, first['id'])
        tasks.set_mode(self.conn, first['id'], value['version'], 'automatic')
        trust_mode.set(self.conn, 'demo', True, '旧组信任不能覆盖任务', leader_session_id=first['leader_session_id'])
        automatic = board.ask(self.conn, first['leader_session_id'], '本任务选择？', ['A', 'B'], 'B',
                              goal_execution_id=one['id'])
        manual = board.ask(self.conn, first['leader_session_id'], '另一个任务审美？', ['A', 'B'], 'A',
                           goal_execution_id=two['id'])
        self.assertEqual(self.conn.execute('SELECT answer FROM decisions WHERE id=?', (automatic,)).fetchone()[0], 'B')
        self.assertIsNone(self.conn.execute('SELECT answer FROM decisions WHERE id=?', (manual,)).fetchone()[0])

    def test_switching_mode_reconsiders_pending_choices_but_preserves_manual_stop(self):
        task = self.make_task()
        execution = self.execution(task)
        decision = board.ask(self.conn, task['leader_session_id'], '待决定路线', ['A', 'B'], 'B',
                             goal_execution_id=execution['id'])
        value = tasks.get(self.conn, task['id'])
        tasks.set_mode(self.conn, task['id'], value['version'], 'automatic')
        self.assertEqual(self.conn.execute('SELECT answer FROM decisions WHERE id=?', (decision,)).fetchone()[0], 'B')
        value = tasks.get(self.conn, task['id'])
        tasks.set_mode(self.conn, task['id'], value['version'], 'manual')
        pending = board.ask(self.conn, task['leader_session_id'], '停止后不能自动拍板', ['A', 'B'], 'A',
                            goal_execution_id=execution['id'])
        goals.request_pause(self.conn, task['id'], manual=True)
        value = tasks.get(self.conn, task['id'])
        changed = tasks.set_mode(self.conn, task['id'], value['version'], 'automatic')
        self.assertEqual(changed['state'], 'stopped')
        self.assertIsNone(self.conn.execute('SELECT answer FROM decisions WHERE id=?', (pending,)).fetchone()[0])

    def test_legacy_goal_migration_is_idempotent_and_does_not_spread_leader_trust(self):
        initial = self.make_task()
        leader = initial['leader_session_id']
        trust_mode.set(self.conn, 'demo', True, '仅旧组原目标', leader_session_id=leader)
        old = goals.create(self.conn, 'demo', leader, '旧目标', '保留原历史', ['检查原历史'])
        tasks.ensure_schema(self.conn)
        tasks.ensure_schema(self.conn)
        migrated = tasks.get(self.conn, old['id'])
        self.assertEqual(migrated['mode'], 'manual')
        self.assertTrue(migrated['authorized'])
        source = json.loads(self.conn.execute('SELECT source_json FROM task_migrations WHERE goal_id=?', (old['id'],)).fetchone()[0])
        self.assertEqual(source['legacy_leader_trust']['enabled'], 1)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM task_migrations WHERE goal_id=?', (old['id'],)).fetchone()[0], 1)
        self.assertEqual(goals.requirements(self.conn, old['id'])['request'], '保留原历史')

    def test_trash_restore_preserves_files_and_is_stopped(self):
        task = self.make_task()
        target = self.project / 'user.txt'
        target.write_text('用户成果', encoding='utf-8')
        trashed = tasks.set_lifecycle(self.conn, task['id'], task['version'], 'trashed')
        self.assertEqual(tasks.list_tasks(self.conn), [])
        self.assertEqual(tasks.list_tasks(self.conn, include_inactive=True)[0]['state'], 'trashed')
        restored = tasks.set_lifecycle(self.conn, task['id'], trashed['version'], 'active')
        self.assertEqual(restored['state'], 'stopped')
        self.assertFalse(restored['authorized'])
        self.assertEqual(target.read_text(encoding='utf-8'), '用户成果')

    def test_invalid_link_rolls_back_entire_task_creation(self):
        with self.assertRaises(board.BoardError):
            self.make_task(projects=['missing'])
        self.assertEqual(tasks.list_tasks(self.conn), [])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)

    def test_example_has_required_real_card_fields(self):
        example = json.loads((Path(__file__).parents[2] / 'docs/fixtures/crew-api.json').read_text(encoding='utf-8'))
        self.assertEqual(set(example['task']), set(self.make_task()))


if __name__ == '__main__':
    unittest.main()
