"""AI work bookkeeping is scoped, dependency-aware and repeatable."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from types import SimpleNamespace
from crew import board, goal_actions, goal_api, goal_work, goals, store
from tests.test_goals import GoalFixture


class GoalWorkTests(GoalFixture):
    def setUp(self):
        super().setUp()
        self.current = self.goal()
        self.gid = self.current['id']

    def add(self, key='design', **options):
        return goal_work.add(self.conn, self.gid, key, options.pop('title', '实现并验证 ' + key),
                             options.pop('acceptance', '通过实际验收'), actor='chief-id', **options)

    def test_concurrent_retries_share_one_item_and_do_not_erase_completed_work(self):
        def create(_):
            with closing(store.connect(self.home)) as conn:
                return goal_work.add(conn, self.gid, 'design', '实现并验证 design', '通过实际验收', actor='chief-id')
        with ThreadPoolExecutor(max_workers=3) as pool:
            values = list(pool.map(create, range(3)))
        self.assertEqual(len({item['id'] for item in values}), 1)
        goal_work.update(self.conn, self.gid, 'design', 1, status='done', note='验收输出 artifacts/design.txt', actor='chief-id')
        self.assertEqual(self.add()['status'], 'done')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_work_history').fetchone()[0], 2)

    def test_dependencies_cannot_cycle_or_allow_unfinished_prerequisites(self):
        first = self.add('data')
        second = self.add('measure', dependencies=['data'])
        with self.assertRaises(board.BoardError):
            goal_work.update(self.conn, self.gid, 'data', 1, dependencies=['measure'], actor='chief-id')
        with self.assertRaises(board.Conflict):
            goal_work.update(self.conn, self.gid, 'measure', 1, status='doing', actor='chief-id')
        goal_work.update(self.conn, self.gid, 'data', 1, status='done', note='测试音频及标签已核验', actor='chief-id')
        goal_work.update(self.conn, self.gid, 'measure', 1, status='doing', actor='chief-id')
        self.assertEqual(goal_work.summary(self.conn, self.gid)['doing'], 1)

    def test_members_can_update_only_assigned_progress_and_cannot_delegate(self):
        item = self.add('measure', owner='member-id')
        with self.assertRaises(board.BoardError):
            goal_work.add(self.conn, self.gid, 'extra', '下发工作', 'pass', actor='member-id')
        with self.assertRaises(board.BoardError):
            goal_work.update(self.conn, self.gid, 'measure', 1, owner='chief-id', actor='member-id')
        with self.assertRaises(board.BoardError):
            goal_work.update(self.conn, self.gid, 'measure', 1, status='done', actor='member-id')
        done = goal_work.update(self.conn, self.gid, 'measure', 1, status='done', note='实际测量见 result.json', actor='member-id')
        self.assertEqual(done['status'], 'done')
        self.add('leader-only')
        with self.assertRaises(board.BoardError):
            goal_work.update(self.conn, self.gid, 'leader-only', 1, status='doing', actor='member-id')

    def test_requirement_change_requires_review_and_history_is_preserved(self):
        self.add()
        item = goal_work.update(self.conn, self.gid, 'design', 1, status='done', note='原验收结果', actor='chief-id')
        goals.amend(self.conn, self.gid, '整段流结束后400ms内返回')
        self.assertTrue(goal_work.items(self.conn, self.gid)[0]['needs_review'])
        self.assertEqual(goal_work.summary(self.conn, self.gid)['done'], 0)
        with self.assertRaises(board.Conflict):
            goal_work.require_complete(self.conn, self.gid)
        checked = goal_work.update(self.conn, self.gid, 'design', item['version'], status='done', note='新测量截止延迟380ms，证据见 measurement.json', actor='chief-id')
        self.assertFalse(checked['needs_review'])
        goal_work.require_complete(self.conn, self.gid)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_work_history').fetchone()[0], 3)

    def test_stale_writes_reject_but_exact_successful_retry_is_passive(self):
        self.add()
        first = goal_work.update(self.conn, self.gid, 'design', 1, status='blocked', note='需要实验数据', actor='chief-id')
        again = goal_work.update(self.conn, self.gid, 'design', 1, status='blocked', note='需要实验数据', actor='chief-id')
        self.assertEqual(again, first)
        with self.assertRaises(board.Conflict):
            goal_work.update(self.conn, self.gid, 'design', 1, status='done', note='迟到的结果', actor='chief-id')

    def test_agent_action_and_ui_projection_share_the_same_checklist(self):
        item = goal_actions.dispatch(self.home, 'chief-id', None, 'work.add',
            {'goal_id': self.gid, 'key': 'app', 'title': '健身打卡 App', 'acceptance': '能记录并展示本周打卡'})
        listed = goal_actions.dispatch(self.home, 'chief-id', None, 'work.list', {'goal_id': self.gid})
        detail = goal_api.detail(self.conn, 'demo', self.gid)
        self.assertEqual(listed['items'][0]['id'], item['id'])
        self.assertEqual(detail['work_items'], listed['items'])
        self.assertEqual(detail['goal']['work_summary']['total'], 1)

    def test_final_delivery_cannot_bypass_unfinished_ai_work(self):
        self.add()
        goals.transition(self.conn, self.gid, 'running')
        with self.assertRaisesRegex(board.Conflict, '工作清单'):
            goal_actions.dispatch(self.home, 'chief-id', None, 'finish', {'goal_id': self.gid, 'report': self.report()}, controller=SimpleNamespace())
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_reports').fetchone()[0], 0)
