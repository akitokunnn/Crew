"""Coverage and field rules for the operation registry (OPERATIONS_REGISTRY_DESIGN §10)."""
import tests
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from crew import board, goal_actions, personal, remote_routes, report_preview, web
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
GOAL_PAGE_ACTIONS = ('mode.set', 'report.publish', 'versions.list', 'versions.preview', 'version.capture', 'version.restore')
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
    def test_http_serves_only_the_operation_entry(self):
        self.assertEqual(routes.post_paths(), report_preview.WORKER_PATHS | {routes.OP_PATH})
        self.assertEqual(routes.get_paths(), expected.TRANSPORT_GET)
        self.assertEqual(routes.get_paths() & web.STATIC.keys(), set())

    def test_every_action_of_the_old_multi_action_urls_is_an_operation(self):
        cases = {'note.': personal.UI_NOTE_ACTIONS, 'tag.': personal.UI_TAG_ACTIONS,
                 'goal.': GOAL_ACTION_VALUES + GOAL_PAGE_ACTIONS, 'goal.context.': ('put', 'correct', 'expire'),
                 'prompt.': PROMPT_POST_VALUES + PROMPT_GET_VALUES + ('list',)}
        for prefix, actions in cases.items():
            for action in actions:
                with self.subTest(op=prefix + action):
                    self.assertIsNotNone(registry.get(prefix + action))

    def test_every_page_call_names_a_registered_operation(self):
        """Design §10 item 3: a page or desktop call outside the registry fails here."""
        repo = Path(__file__).resolve().parents[2]
        files = [*repo.glob('frontend/*.js'), *repo.glob('frontend/*.html'), *repo.glob('desktop/src/*.cjs'),
                 *repo.glob('desktop/src/*.js'), *repo.glob('desktop/src/*.html')]
        ops = registry.operations()
        literal = re.compile(r"""['"`]/api/op/([a-z_.]+?)(?=['"`?])""")
        dynamic = re.compile(r"""['"`]/api/op/([a-z_.]+\.)['"`]\s*\+""")
        other = re.compile(r"""['"`](/api/[A-Za-z0-9/_-]+)""")
        used = set()
        for file in files:
            source = file.read_text(encoding='utf-8')
            declared = {name for line in re.findall(r'crew-op:([^\n]*)', source) for name in line.split()}
            for match in other.findall(source):
                if not match.startswith(('/api/op', '/api/report-preview/worker/')):
                    self.assertIn(match, expected.TRANSPORT_GET | report_preview.WORKER_PATHS | {'/api/access/context'},
                                  '%s calls an address outside /api/op: %s' % (file.name, match))
            for prefix in dynamic.findall(source):
                self.assertTrue(any(name.startswith(prefix) for name in declared),
                                '%s builds /api/op/%s… without a crew-op comment' % (file.name, prefix))
            for name in {name for name in literal.findall(source) if not name.endswith('.')} | declared:
                with self.subTest(file=file.name, op=name):
                    self.assertTrue(name in ops, '%s calls an unregistered operation %s' % (file.name, name))
                    self.assertTrue(ops[name].scopes & {'human', 'device_browser', 'device_desktop'})
                    used.add(name)
        self.assertGreater(len(used), 100)

    def test_every_goal_action_is_an_ai_operation(self):
        renamed = {'status': 'goal.get', 'report-template': 'goal.report.template'}
        for action in goal_actions.GOAL_ACTIONS | goal_actions.CONTEXT_ACTIONS:
            with self.subTest(action=action):
                op = registry.get(renamed.get(action, 'goal.' + action))
                self.assertIsNotNone(op)
                self.assertTrue(op.scopes & {'leader', 'execution'})

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

    def test_desktop_worker_actions_are_operations_or_local(self):
        repo = Path(__file__).resolve().parents[2]
        source = (repo / 'desktop' / 'src' / 'operations.cjs').read_text(encoding='utf-8')
        actions = set(re.findall(r"'([a-z-]+)'", re.search(r'const ACTIONS = new Set\(\[([^\]]*)\]\)', source).group(1)))
        bound = {binding.split(':', 1)[1]: op.name for op in registry.operations().values() for binding in op.ipc}
        self.assertEqual(actions, set(bound) | set(expected.LOCAL_WORKER_ACTIONS))
        self.assertEqual(set(bound) & set(expected.LOCAL_WORKER_ACTIONS), set())

    def test_cli_is_generated_from_human_operations(self):
        import argparse
        from crew import cli
        from crew.ops import cli as ops_cli
        root = ops_cli.parser()
        leaves = set()

        def walk(parser, path):
            for action in parser._actions:
                if isinstance(action, argparse._SubParsersAction):
                    for name, child in action.choices.items():
                        walk(child, path + (name,))
                    return
            leaves.add(path)
        walk(root, ())
        human = {op.name for op in registry.operations().values() if 'human' in op.scopes}
        self.assertEqual({'.'.join(path) for path in leaves}, human)
        self.assertEqual(set(ops_cli.HOST_COMMANDS), {'web', 'start', 'install', 'autostart', 'menu', 'doctor', 'usage'})
        host = {action.dest for action in cli.parser()._actions if isinstance(action, argparse._SubParsersAction)}
        self.assertEqual(host, {'command'})
        self.assertNotIn('join', ops_cli.HOST_COMMANDS)

    def test_registration_rejects_incomplete_entries(self):
        good = registry.get('channel.post') if registry.operations() else None
        from dataclasses import replace
        for change, message in (({'scopes': frozenset()}, 'scopes'), ({'access': 'maybe'}, 'access'),
                                ({'name': 'Bad Name'}, 'lowercase'), ({'lock': 'outer', 'project': 'selected'}, 'project'),
                                ({'params': {'type': 'object', 'patternProperties': {}}}, 'unsupported')):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                registry.register(replace(good, name=change.get('name', 'test.bad'), **{
                    key: value for key, value in change.items() if key != 'name'}))

    def test_remote_device_scopes_match_frozen_rules(self):
        ops = registry.operations()
        self.assertEqual({name for name, op in ops.items() if 'device_browser' in op.scopes}, expected.DEVICE_BROWSER)
        self.assertEqual({name for name, op in ops.items() if 'device_desktop' in op.scopes}, expected.DEVICE_DESKTOP)
        for kind in ('browser', 'desktop'):
            self.assertTrue(remote_routes.permitted('POST', '/api/op', web.STATIC, kind))
            self.assertTrue(remote_routes.permitted('GET', '/api/op/channel.list', web.STATIC, kind))
            self.assertFalse(remote_routes.permitted('POST', '/mcp', web.STATIC, kind))
        self.assertFalse(remote_routes.permitted('GET', '/api/settings', web.STATIC, 'desktop'))
        self.assertTrue(remote_routes.permitted('GET', '/api/admin/health', web.STATIC, 'desktop'))
        self.assertFalse(remote_routes.permitted('GET', '/api/admin/health', web.STATIC, 'browser'))

    def test_devices_cannot_name_an_ai_identity(self):
        from crew.ops import scopes
        device = Caller('device_browser', device_id='d1')
        for args in ({'caller_session_id': 's3'}, {'data': {'execution_id': 'e1'}}, {'actor_session_id': 's3'}, {'caller_execution_id': 'e1'}):
            with self.subTest(args=args), self.assertRaises(OpError):
                scopes.human_payload(registry.get('goal.mode.set'), device, args)
        scopes.human_payload(registry.get('goal.mode.set'), device, {'actor_session_id': 'user', 'data': {}})
        with self.assertRaises(OpError):
            scopes.human_payload(registry.get('leader.create'), device, {'candidate_members': []})

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
        with self.assertRaises(OpError) as caught:
            self.call('decision.answer', {'id': 'not-a-number', 'text': 'x'}, 'r-2')
        self.assertEqual(caught.exception.code, 'invalid_arguments')
        with self.assertRaisesRegex(board.BoardError, '999999'):
            self.call('decision.answer', {'id': 999999, 'text': 'x'}, 'r-3')
        with self.assertRaises(OpError) as caught:
            self.call('decision.answer', {'id': 999999, 'text': 'x'}, 'r-3')
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
        code, page = self.request('/api/op/channel.post?project=demo', {'body': '页面形式'})
        self.assertEqual(code, 400)
        code, gone = self.request('/api/channel?project=demo', {'project': 'demo', 'body': '旧地址'})
        self.assertEqual(code, 404)


if __name__ == '__main__':
    unittest.main()
