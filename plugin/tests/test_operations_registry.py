"""Coverage and field rules for the operation registry (OPERATIONS_REGISTRY_DESIGN §10)."""
import tests
from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from crew import goal_actions, personal, remote_routes, report_preview, web
from crew.ops import executor, registry
from crew.ops.caller import Caller, HUMAN
from crew.ops.errors import OpError
from crew.web_api import routes
import operations_expected as expected
from test_shell import NOW, PLUGIN, ShellFixture
from crew import store

# Concrete action values each multi-action URL accepts, read from the code that dispatches them.
GOAL_ACTION_VALUES = ('accept', 'reject', 'observe', 'pause', 'start', 'resume', 'cancel', 'reopen', 'transfer')
GOAL_CONTEXT_VALUES = ('add', 'correct', 'expire')
PROMPT_POST_VALUES = ('save', 'reset')
PROMPT_GET_VALUES = ('get', 'history', 'applied')
# Reads that still write today. Each belongs to a later step and may only shrink.
READS_WITH_WRITES = {
    'workspace.state': 'state() 先扫描用量入库（读路径去副作用，增量事件流主题）',
    'document.list': 'GET /api/documents 持写锁刷新索引',
    'tags.list': '首次读取时补登记旧标签',
    'assistant.state': '首次读取时创建助手',
    'usage.report': '读取前扫描原生日志入库',
    'usage.export': '读取前扫描原生日志入库',
}


class RegistryCoverageTests(unittest.TestCase):
    def test_every_business_route_is_an_operation_binding(self):
        self.assertEqual(registry.paths('POST'), expected.POST)
        self.assertEqual(registry.paths('GET'), expected.GET)
        self.assertEqual(routes.post_paths(), expected.POST | report_preview.WORKER_PATHS | {routes.OP_PATH})
        self.assertEqual(routes.get_paths(), expected.GET | expected.TRANSPORT_GET)
        self.assertEqual(routes.get_paths() & web.STATIC.keys(), set())

    def test_every_action_of_a_multi_action_url_is_bound(self):
        bound = {}
        for (method, path, when), op in registry.bindings().items():
            bound.setdefault((method, path), {})[dict(when).get('action')] = op.name
        cases = {('POST', '/api/personal/notes'): personal.UI_NOTE_ACTIONS,
                 ('POST', '/api/personal/tags'): personal.UI_TAG_ACTIONS,
                 ('POST', '/api/goals/agent'): goal_actions.GOAL_ACTIONS | goal_actions.CONTEXT_ACTIONS,
                 ('POST', '/api/goals/action'): GOAL_ACTION_VALUES,
                 ('POST', '/api/goals/context'): GOAL_CONTEXT_VALUES,
                 ('POST', '/api/behavior-prompts'): PROMPT_POST_VALUES,
                 ('GET', '/api/behavior-prompts'): PROMPT_GET_VALUES + (None,)}
        for address, actions in cases.items():
            with self.subTest(address=address):
                self.assertEqual(set(bound[address]), set(actions))

    def test_bindings_resolve_by_discriminator(self):
        op, binding = registry.resolve('POST', '/api/goals/agent', {'action': 'start'})
        self.assertEqual((op.name, binding.path), ('goal.start', '/api/goals/agent'))
        op, binding = registry.resolve('POST', '/api/goals/action', {'action': 'start'})
        self.assertEqual((op.name, binding.path), ('goal.start', '/api/goals/action'))
        self.assertEqual(registry.resolve('GET', '/api/behavior-prompts', {})[0].name, 'prompt.list')
        self.assertEqual(registry.resolve('GET', '/api/behavior-prompts', {'action': 'history'})[0].name, 'prompt.history')
        self.assertIsNone(registry.resolve('POST', '/api/personal/notes', {'action': 'nonsense'}))

    def test_field_rules(self):
        tools = set()
        for op in registry.operations().values():
            with self.subTest(op=op.name):
                self.assertNotIn(op.tool_name, tools)
                tools.add(op.tool_name)
                self.assertEqual(op.hints.read_only, op.access == 'read')
                if op.risk == 'high':
                    self.assertTrue(op.hints.destructive)
                if op.access != 'read':
                    self.assertTrue(op.emits)
        self.assertLessEqual(set(READS_WITH_WRITES), {name for name, op in registry.operations().items()
                                                      if op.access == 'read'})

    def test_registration_rejects_incomplete_entries(self):
        good = registry.get('channel.post') if registry.operations() else None
        from dataclasses import replace
        for change, message in (({'scopes': frozenset()}, 'scopes'), ({'access': 'maybe'}, 'access'),
                                ({'name': 'Bad Name'}, 'lowercase'), ({'lock': 'outer', 'project': 'selected'}, 'project'),
                                ({'params': {'type': 'object', 'patternProperties': {}}}, 'unsupported')):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                registry.register(replace(good, name=change.get('name', 'test.bad'), http=(), **{
                    key: value for key, value in change.items() if key != 'name'}))

    def test_remote_device_scopes_match_route_rules(self):
        """A device may call an operation when the remote rules admit at least one of its addresses."""
        def admitted(binding, kind):
            allowed = remote_routes.permitted(binding.method, binding.path, web.STATIC, kind)
            if binding.path == '/api/goals/agent':
                allowed = allowed and dict(binding.when)['action'] in remote_routes.HUMAN_GOAL_ACTIONS
            return allowed

        for op in registry.operations().values():
            with self.subTest(op=op.name):
                self.assertEqual('device_browser' in op.scopes, any(admitted(b, 'browser') for b in op.http))
                self.assertEqual('device_desktop' in op.scopes, any(admitted(b, 'desktop') for b in op.http))

class OperationCallTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.service = web.Service(
            self.home, PLUGIN, cwd=self.project, agents_provider=lambda: [],
            process_factory=self.process_factory, now=lambda: store.parse_iso(NOW),
            claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
            vscode_state_db=self.vscode_state_db, isolated_sources=True)
        self.addCleanup(self.service.close)

    def call(self, name, args, request_id=None, caller=HUMAN):
        return executor.call(self.service, caller, name, args, request_id=request_id, project_name='demo')

    def test_write_runs_once_per_request_id(self):
        first = self.call('channel.post', {'project': 'demo', 'body': '第一条'}, 'r-1')
        again = self.call('channel.post', {'project': 'demo', 'body': '第一条'}, 'r-1')
        self.assertTrue(again['replayed'])
        self.assertEqual(again['message']['id'], first['message']['id'])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM channel_messages WHERE body='第一条'").fetchone()[0], 1)
        with self.assertRaises(OpError) as caught:
            self.call('channel.post', {'project': 'demo', 'body': '换了内容'}, 'r-1')
        self.assertEqual(caught.exception.code, 'request_id_reused')

    def test_write_requires_request_id_and_failures_replay(self):
        with self.assertRaises(OpError) as caught:
            self.call('channel.post', {'project': 'demo', 'body': 'x'})
        self.assertEqual(caught.exception.code, 'invalid_arguments')
        with self.assertRaises(ValueError):
            self.call('decision.answer', {'id': 'not-a-number', 'text': 'x'}, 'r-2')
        with self.assertRaises(OpError) as caught:
            self.call('decision.answer', {'id': 'not-a-number', 'text': 'x'}, 'r-2')
        self.assertEqual((caught.exception.code, caught.exception.detail['replayed']), ('invalid_arguments', True))

    def test_reads_leave_no_receipt_and_roles_are_checked(self):
        state = self.call('todo.list', {})
        self.assertEqual(state, {'todos': []})
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM op_requests').fetchone()[0], 0)
        with self.assertRaises(OpError) as caught:
            self.call('remote_access.get', {}, caller=Caller('device_browser', device_id='d1'))
        self.assertEqual(caught.exception.code, 'forbidden')
        with self.assertRaises(OpError) as caught:
            self.call('no.such', {})
        self.assertEqual(caught.exception.code, 'not_found')

    def test_split_actions_rebuild_the_old_body(self):
        note = self.service.op('note.capture', {'data': {'text': '一个想法'}})
        self.assertEqual(note['note']['original_text'], '一个想法')
        replay = self.service.op('note.capture', {'data': {'text': '一个想法'}}, request_id='n-1')
        again = self.service.op('note.capture', {'data': {'text': '一个想法'}}, request_id='n-1')
        self.assertEqual(again['note']['id'], replay['note']['id'])


class OperationHttpTests(OperationCallTests):
    def setUp(self):
        super().setUp()
        server = ThreadingHTTPServer(('127.0.0.1', 0), web.Handler)
        server.service = self.service
        self.url = 'http://127.0.0.1:%d' % server.server_port
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)

    def request(self, path, body=None):
        data = None if body is None else json.dumps(body).encode('utf-8')
        try:
            response = urlopen(Request(self.url + path, data, {'X-Crew': '1'}), timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.loads(response.read())

    def test_post_and_get_entries(self):
        code, posted = self.request('/api/op', {'op': 'channel.post', 'project': 'demo', 'request_id': 'h-1',
                                                'args': {'project': 'demo', 'body': '从 /api/op 发'}})
        self.assertEqual(code, 200, posted)
        code, replay = self.request('/api/op', {'op': 'channel.post', 'project': 'demo', 'request_id': 'h-1',
                                                'args': {'project': 'demo', 'body': '从 /api/op 发'}})
        self.assertEqual((code, replay['replayed'], replay['message']['id']), (200, True, posted['message']['id']))
        code, listed = self.request('/api/op/channel.list?project=demo')
        self.assertEqual(code, 200)
        self.assertIn('从 /api/op 发', json.dumps(listed, ensure_ascii=False))
        code, refused = self.request('/api/op', {'op': 'channel.post', 'args': {}, 'extra': 1})
        self.assertEqual((code, refused['code']), (400, 'invalid_arguments'))
        code, missing = self.request('/api/op/channel.post?project=demo')
        self.assertEqual((code, missing['code']), (404, 'not_found'))


if __name__ == '__main__':
    unittest.main()
