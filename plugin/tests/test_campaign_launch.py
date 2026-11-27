"""Assistant-led opening: one confirmed plan builds the board, leaders, first tasks and the war room."""
import tests  # Install the production-service connection guard.
import json
import unittest
from unittest import mock

from crew import board, domain_versions, goals, personal, store, war_room, web
from crew.ops import confirmations, executor
from ops_helpers import ai_call
from test_shell import NOW, PLUGIN, ShellFixture


def plan(**extra):
    value = {
        'note': '两位 leader 起盘登录模块',
        'parts': [{'ref': 'login01', 'name': '登录', 'parent': None}],
        'leaders': [{'name': '后端', 'brief': '负责登录接口与签名校验', 'runtime': 'claude', 'part': 'login01', 'lead': True},
                    {'name': '页面', 'brief': '负责登录页与调用', 'runtime': 'claude', 'part': 'login01'}],
        'goals': [{'leader': '后端', 'title': '登录接口', 'request': '实现 POST /login', 'acceptance': ['接口返回 token']},
                  {'leader': '页面', 'title': '登录页', 'request': '做登录表单并调接口', 'acceptance': ['能登录成功']}],
        'room': {'title': '登录起盘', 'goal': '两端联调通过登录', 'budget_tokens': 200000},
        'start': True}
    value.update(extra)
    return value


class CampaignTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.service = web.Service(
            self.home, PLUGIN, cwd=self.project, agents_provider=lambda: [],
            process_factory=self.process_factory, now=lambda: store.parse_iso(NOW),
            claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
            vscode_state_db=self.vscode_state_db, isolated_sources=True)
        self.addCleanup(self.service.close)
        executable = mock.patch('crew.chat.shutil.which', return_value='claude.exe')
        executable.start()
        self.addCleanup(executable.stop)

    def call(self, name, args):
        return executor.human_call(self.service, name, {'project': 'demo', **args}, project='demo')

    def test_preview_lists_what_it_creates_and_what_it_may_cost(self):
        (self.home / 'prices.json').write_text(json.dumps({'claude-haiku-5-5': {
            'input': 1.0, 'output': 5.0, 'cache_write_5m': 1.25, 'cache_write_1h': 2.0, 'cache_read': 0.1}}),
            encoding='utf-8')
        leaders = [{**leader, 'model': 'claude-haiku-5-5'} for leader in plan()['leaders']]
        value = self.call('campaign.preview', plan(leaders=leaders))
        self.assertTrue(value['ok'], value['errors'])
        cost = value['cost']
        self.assertEqual((cost['new_leaders'], cost['budget_tokens'], cost['start']), (2, 200000, True))
        self.assertEqual(cost['carriers'], [{'runtime': 'claude', 'model': 'claude-haiku-5-5', 'effort': '默认强度', 'count': 2}])
        self.assertEqual((cost['estimate']['low_usd'], cost['estimate']['high_usd']), (0.2, 1.0))
        self.assertIn('立即启动 2 位 leader', cost['note'])

    def test_preview_names_every_problem(self):
        bad = plan(parts=[{'ref': 'x', 'name': '登录'}],
                   leaders=[{'name': 'lead', 'brief': '已有的名字', 'part': 'nowhere'}],
                   goals=[{'leader': '没人', 'title': 't', 'request': 'r', 'acceptance': ['a']}])
        value = self.call('campaign.preview', bad)
        self.assertFalse(value['ok'])
        text = '\n'.join(value['errors'])
        for fragment in ('ref 要是', '领域 nowhere 不存在', '负责人要是本方案里新建的 leader', '至少要两位新 leader'):
            self.assertIn(fragment, text)

    def test_launch_builds_everything_as_one_version_and_opens_the_room(self):
        checked = self.call('campaign.preview', plan())
        result = self.call('campaign.launch', {**plan(), 'board_version': checked['board_version']})
        self.assertTrue(result['started'])
        by_name = {leader['name']: leader['session_id'] for leader in result['leaders']}
        self.assertEqual(set(by_name), {'后端', '页面'})
        versions = domain_versions.listing(self.conn, 'demo')['versions']
        self.assertEqual([(v['source'], v['parts'], v['leaders']) for v in versions][:1], [('restructure', 1, 2)])
        self.assertEqual([goal['title'] for goal in result['goals']], ['登录接口', '登录页'])
        for goal in result['goals']:
            self.assertNotEqual(goals.get(self.conn, goal['id'])['status'], 'draft')
        room = war_room.get(self.conn, result['room']['id'])
        self.assertEqual(room['budget']['tokens'], 200000)
        self.assertEqual({m['crew_name'] for m in room['members']}, {'后端', '页面'})
        # Running the same plan again is refused: its parts and leader names now exist.
        with self.assertRaises(board.BoardError):
            self.call('campaign.launch', {**plan(), 'board_version': checked['board_version']})

    def test_structure_only_starts_no_model(self):
        checked = self.call('campaign.preview', plan(start=False, room=None))
        before = len(self.processes)
        result = self.call('campaign.launch', {**plan(start=False, room=None), 'board_version': checked['board_version']})
        self.assertFalse(result['started'])
        self.assertEqual([leader['name'] for leader in result['to_start']], ['后端', '页面'])
        self.assertEqual(len(self.processes), before)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM sessions WHERE crew_name='后端'").fetchone())

    def test_the_assistant_waits_for_one_confirmation_and_then_leads_the_room(self):
        assistant = personal.ensure_assistant(self.conn, self.home)['session_id']
        checked = self.call('campaign.preview', plan())
        with mock.patch('crew.personal.instruction_for_user_action', return_value={'instruction': '开局'}):
            asked = ai_call(self.service, self.conn, assistant, 'campaign.launch',
                            {**plan(), 'project': 'demo', 'board_version': checked['board_version']}, message_id='m1')
        self.assertEqual(asked['status'], 'awaiting_user')
        self.assertIsNone(self.conn.execute("SELECT 1 FROM sessions WHERE crew_name='后端'").fetchone())
        with mock.patch.object(confirmations, '_tell'):
            done = confirmations.resolve(self.service, asked['confirmation_id'], True, NOW)
        self.assertEqual(done['status'], 'approved', done['error'])
        room = war_room.get(self.conn, done['result']['room']['id'])
        self.assertEqual((room['lead_session_id'], room['lead_name']), (assistant, '助手'))


if __name__ == '__main__':
    unittest.main()
