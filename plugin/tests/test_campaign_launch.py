"""Assistant-led opening: one confirmed plan builds the board, leaders, first tasks and the war room."""
import tests  # Install the production-service connection guard.
import json
import unittest
from unittest import mock

from crew import board, domain_versions, goals, personal, store, war_room, web
from crew.ops import confirmations, executor
from crew.ops.errors import OpError
from ops_helpers import ai_call
from test_shell import NOW, PLUGIN, ShellFixture


def plan(**extra):
    value = {
        'note': '两位 leader 起盘登录模块',
        'parts': [{'ref': 'login01', 'name': '登录', 'parent': None}],
        'leaders': [{'name': '后端', 'brief': '负责登录接口与签名校验', 'runtime': 'claude', 'part': 'login01', 'lead': True,
                     'owns': ['server/', 'data/users.json']},
                    {'name': '页面', 'brief': '负责登录页与调用', 'runtime': 'claude', 'part': 'login01', 'owns': ['web/']}],
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

    def test_leaders_sharing_the_workspace_must_split_its_files(self):
        # Scenario 8: two new leaders in one folder, no written split, and the front end rewrote the back end's data.
        def leaders(first, second):
            back = {key: value for key, value in plan()['leaders'][0].items() if key != 'owns'}
            return [back if first is None else {**back, 'owns': first}, {**plan()['leaders'][1], 'owns': second}]
        missing = self.call('campaign.preview', plan(leaders=leaders(None, ['web/'])))
        self.assertEqual(missing['errors'], ['「后端」：几位 leader 共用工作区目录，要用 owns 写明它负责的文件或目录'])
        overlap = self.call('campaign.preview', plan(leaders=leaders(['data/'], ['web\\', './data/products.json'])))
        self.assertEqual(overlap['errors'], ['「后端」的 data/ 和「页面」的 data/products.json 重叠：一个文件只归一位 leader'])
        for bad, fragment in ((['/etc/x'], '相对工作区根目录'), (['C:\\x'], '相对工作区根目录'), (['../x'], '上级目录'),
                              (['.'], '整个工作区'), (['a', './a'], '写了两次')):
            with self.subTest(bad=bad):
                value = self.call('campaign.preview', plan(leaders=leaders(bad, ['web/'])))
                self.assertFalse(value['ok'])
                self.assertEqual(len(value['errors']), 1, value['errors'])
                self.assertIn(fragment, value['errors'][0])
        for empty in ([], [' '], None):
            with self.subTest(empty=empty), self.assertRaises(OpError):
                self.call('campaign.preview', plan(leaders=[{**plan()['leaders'][0], 'owns': empty}, plan()['leaders'][1]]))
        clean = self.call('campaign.preview', plan(leaders=leaders(['server/', 'data\\users.json'], ['web/', 'data/products.json'])))
        self.assertTrue(clean['ok'], clean['errors'])
        self.assertEqual([leader['owns'] for leader in clean['leaders']], [['server/', 'data/users.json'], ['web/', 'data/products.json']])
        # One new leader has nobody to split with; a split it is given is still carried.
        alone = {key: value for key, value in plan()['leaders'][0].items() if key != 'owns'}
        self.assertTrue(self.call('campaign.preview', plan(leaders=[alone], goals=plan()['goals'][:1], room=None))['ok'])

    def test_launch_hands_each_leader_its_file_split_in_the_opening_and_the_first_task(self):
        checked = self.call('campaign.preview', plan())
        with mock.patch.object(self.service.chat, 'start', wraps=self.service.chat.start) as start:
            result = self.call('campaign.launch', {**plan(), 'board_version': checked['board_version']})
        briefs = {call.args[2]: call.args[3] for call in start.call_args_list}
        self.assertTrue(briefs['后端'].startswith('负责登录接口与签名校验\n\n文件分工'))
        self.assertIn('你负责 server/、data/users.json；「页面」负责 web/。只改自己负责的', briefs['后端'])
        self.assertIn('你负责 web/；「后端」负责 server/、data/users.json。', briefs['页面'])
        room = result['room']['id']
        rules = {}
        for task in result['goals']:
            constraints = goals.requirements(self.conn, task['id'])['constraints']
            self.assertEqual(constraints[0], briefs[task['leader']].split('\n\n', 1)[1])
            self.assertEqual(len(constraints), 2)
            rules[task['leader']] = constraints[1]
        # Room posts reach the leader's own conversation, not the task's execution: the task names its room. The user
        # opened this campaign, so the first leader leads the room and sends the card itself.
        self.assertEqual(rules['页面'], '这个任务属于作战室 #%d《登录起盘》（牵头：后端）：和别的成员冲突、要用户定的事，'
                                        '当场用 `war_room_post`（room=%d）点名牵头和对方，写清分歧、方案和推荐，由牵头发拍板卡；'
                                        '不在本任务里先问用户、不自己 `decision_ask`。' % (room, room))
        self.assertEqual(rules['后端'], '这个任务属于作战室 #%d《登录起盘》，你是牵头：和成员冲突、要用户定的事，在房里（room=%d）'
                                        '点名对方核对清楚后，马上用 `war_room_decide` 发拍板卡；不在本任务里先问用户、'
                                        '不自己 `decision_ask`。' % (room, room))

    def test_without_a_room_the_first_task_carries_only_the_file_split(self):
        checked = self.call('campaign.preview', plan(room=None))
        result = self.call('campaign.launch', {**plan(room=None), 'board_version': checked['board_version']})
        self.assertIsNone(result['room'])
        for task in result['goals']:
            constraints = goals.requirements(self.conn, task['id'])['constraints']
            self.assertEqual(len(constraints), 1)
            self.assertTrue(constraints[0].startswith('文件分工'))

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
        # Both leaders are members of the assistant's room: each first task sends conflicts to the room, not the user.
        for task in done['result']['goals']:
            rule = goals.requirements(self.conn, task['id'])['constraints'][1]
            self.assertIn('（牵头：助手）', rule)
            self.assertIn('`war_room_post`（room=%d）点名牵头和对方' % room['id'], rule)


    def test_the_assistant_leads_its_own_room_without_a_fresh_user_message_but_no_other(self):
        assistant = personal.ensure_assistant(self.conn, self.home)['session_id']
        checked = self.call('campaign.preview', plan())
        result = self.call('campaign.launch', {**plan(), 'board_version': checked['board_version']})
        other = war_room.start(self.conn, 'demo', board.HUMAN, '别的房', '另一件事', ['后端', '页面'], lead='后端', now=NOW)
        own = war_room.start(self.conn, 'demo', assistant, '自己的房', '盯首轮', ['后端', '页面'], now=NOW)
        posted = ai_call(self.service, self.conn, assistant, 'war_room.post',
                         {'project': 'demo', 'room': own['id'], 'body': '@后端 接口好了就在状态板标完成'})
        self.assertEqual(posted['message']['speaker_name'], '助手')
        with self.assertRaises(board.BoardError):
            ai_call(self.service, self.conn, assistant, 'war_room.post', {'project': 'demo', 'room': other['id'], 'body': '插一句'})
        closed = ai_call(self.service, self.conn, assistant, 'war_room.close',
                         {'project': 'demo', 'room': own['id'], 'summary': '首轮交付，交棒'})
        self.assertEqual(closed['status'], 'closed')
        self.assertEqual(result['started'], True)


if __name__ == '__main__':
    unittest.main()
