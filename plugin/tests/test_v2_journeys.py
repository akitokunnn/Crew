"""Two product journeys through real HTTP/SQLite and fake native carriers.

The scripted actor stands in for model decisions; these tests make no claim
that a live model built an app or met a diarization latency target.
"""
import time
import threading
import unittest
from unittest import mock
import tests
from crew import goals, store, web
from test_shell import ShellFixture, PLUGIN, NOW
import test_assistant_http as assistant_helpers
from test_codex_runtime import FakeClient


class V2JourneyTests(ShellFixture, unittest.TestCase):
    request = assistant_helpers.AssistantHttpTests.request
    close_server = assistant_helpers.AssistantHttpTests.close_server
    finish_turn = assistant_helpers.AssistantHttpTests.finish_turn

    def setUp(self):
        super().setUp()
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=lambda: [], claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start(); self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = self.server.url

    def post(self, path, data):
        code, value, _ = self.request(path + '?project=demo', data)
        self.assertEqual(code, 200, value)
        return value

    def agent(self, sid, action, data):
        return self.post('/api/goals/agent', {'actor_session_id': sid, 'action': action, 'data': data})

    def complete_turn(self, key, runtime):
        running = self.server.service.chat.processes[key]
        if runtime == 'codex':
            running.client.finish()
        else:
            self.finish_turn(key, running.process)

    def report(self, outcome='progress', criterion='daily', status='simulated'):
        return {'outcome': outcome, 'summary': '隔离工作流演练：每日打卡页面与数据保存已交接。',
            'delivered': ['仅用于工作流测试的本地打卡页面'], 'unresolved': [],
            'acceptance': [{'criterion_id': criterion, 'status': status,
                            'evidence': [{'label': '隔离模拟证据', 'reference': 'fixture/checkins.html'}]}],
            'examples': [{'input': '记录今天', 'expected': '今天显示已打卡', 'actual': '脚本模拟已打卡',
                          'conclusion': '工作流通过，非真实模型产物验收', 'kind': 'illustrative'}],
            'artifacts': ['fixture/checkins.html'], 'tradeoffs': ['仅验证 Crew 任务闭环'],
            'follow_up': '真实成果需另做交互验收',
            'investment': {'elapsed_seconds': 1, 'usage': None, 'roles': [{'role': 'leader', 'contribution': '脚本驱动工作流'}]}}

    def fitness(self, runtime):
        if runtime == 'codex':
            class Client(FakeClient):
                counter = 0
                def __init__(self, **options):
                    super().__init__(**options)
                    type(self).counter += 1
                    self.thread_id = 'journey-native-' + str(type(self).counter)
            patch = mock.patch('crew.codex_runtime._ObservedClient', Client)
            patch.start(); self.addCleanup(patch.stop)
        idea = '我最近打算健身，简单给我一个打卡 App 吧'
        opened = self.post('/api/leader', {'task': idea, 'runtime': runtime})
        sid = opened['session_id']
        self.assertEqual(goals.list_goals(self.conn, 'demo'), [], 'Discussion itself does not create/execute a task')
        self.complete_turn(sid, runtime)
        consent = self.post('/api/chat/send', {'session_id': sid, 'text': '先做每日打卡和本周记录，可以开始。', 'message_id': 'fitness-consent'})
        self.complete_turn(sid, runtime)
        task = self.agent(sid, 'create', {'title': '健身打卡初版', 'request': idea,
            'acceptance': [{'id': 'daily', 'title': '能记录今天并查看本周打卡'}],
            'source': {'message_id': consent['message_id']}})
        gid = task['id']
        item = self.agent(sid, 'work.add', {'goal_id': gid, 'key': 'checkin', 'title': '实现每日打卡',
                                         'acceptance': '按钮能记录并展示本周结果'})
        started = self.post('/api/chat/send', {'session_id': sid, 'goal_id': gid, 'text': '按已确认范围执行', 'message_id': 'fitness-work'})
        duplicate = self.post('/api/chat/send', {'session_id': sid, 'goal_id': gid, 'text': '按已确认范围执行', 'message_id': 'fitness-work'})
        self.assertEqual(started['execution_id'], duplicate['execution_id'])
        stage = self.agent(sid, 'report.publish', {'goal_id': gid, 'request_id': 'stage-1', 'report': self.report()})
        self.assertEqual(goals.get(self.conn, gid)['status'], 'running')
        self.assertFalse(stage['pending_finish'])
        self.agent(sid, 'work.update', {'goal_id': gid, 'key': 'checkin', 'version': item['version'],
            'status': 'done', 'note': '工作流演练中的假载体产物，未调用真实 AI'})
        finished = self.agent(sid, 'finish', {'goal_id': gid, 'report': self.report('awaiting_acceptance')})
        self.assertTrue(finished['pending_finish'])
        self.complete_turn(started['execution_id'], runtime)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and goals.get(self.conn, gid)['status'] != 'awaiting_acceptance':
            self.server.service.chat.goal_controller.sync(self.conn); time.sleep(.02)
        current = goals.get(self.conn, gid)
        self.assertEqual(current['status'], 'awaiting_acceptance')
        self.post('/api/goals/action', {'goal_id': gid, 'action': 'accept', 'version': current['version'], 'note': '隔离流程已核对'})
        self.assertEqual(goals.get(self.conn, gid)['status'], 'completed')
        self.post('/api/chat/send', {'session_id': sid, 'goal_id': gid, 'text': '再增加连续打卡天数', 'message_id': 'fitness-feedback'})
        self.assertEqual(goals.get(self.conn, gid)['current_round'], 2)
        code, detail, _ = self.request('/api/goal?project=demo&id=' + gid)
        self.assertEqual(code, 200, detail)
        self.assertEqual(len(detail['reports']), 2)
        self.assertTrue(all(report['round'] == 1 for report in detail['reports']))
        self.assertEqual(goals.get(self.conn, gid)['original_request'], idea)

    def test_fitness_closed_loop_on_cc(self):
        self.fitness('claude')

    def test_fitness_closed_loop_on_codex(self):
        self.fitness('codex')

    def test_streaming_research_keeps_unknown_result_and_precise_deadline(self):
        request = '分辨 A、B、重叠发言的片段；截止结果必须在整段流式结束后400ms内返回'
        task = self.agent('s3', 'create', {'title': '流式说话人分段研究', 'request': request,
            'acceptance': [{'id': 'latency', 'title': request}], 'constraints': ['不得将模拟结果视为真实声纹达标']})
        item = self.agent('s3', 'work.add', {'goal_id': task['id'], 'key': 'measure',
            'title': '准备带重叠标注的音频并测量最终截止延迟', 'acceptance': request})
        self.post('/api/chat/send', {'session_id': 's3', 'goal_id': task['id'], 'text': '先评估可行性', 'message_id': 'research-start'})
        self.agent('s3', 'work.update', {'goal_id': task['id'], 'key': 'measure', 'version': item['version'],
            'status': 'blocked', 'note': '缺少获得授权且有重叠片段标签的测试数据'})
        report = self.report(criterion='latency', status='unverified')
        report.update(summary='尚不能证明说话人分段和400ms截止要求可达标。',
                      delivered=['待验证的实验方案'], unresolved=['缺少真实标注数据，尚未实测延迟'])
        self.agent('s3', 'report.publish', {'goal_id': task['id'], 'request_id': 'research-stage', 'report': report})
        code, detail, _ = self.request('/api/goal?project=demo&id=' + task['id'])
        self.assertEqual(code, 200, detail)
        self.assertEqual(detail['goal']['request'], request)
        self.assertEqual(detail['work_items'][0]['status'], 'blocked')
        self.assertEqual(detail['reports'][0]['acceptance_counts']['unverified'], 1)
        self.assertNotEqual(detail['goal']['status'], 'completed')
