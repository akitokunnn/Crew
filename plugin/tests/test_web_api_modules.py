"""Feature registration is exercised through the production HTTP transport."""
import tests
import json
import threading
import unittest
from contextlib import contextmanager
from unittest import mock
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from crew import web
from crew.web_api import Content, Registry, Request as ApiRequest
from test_shell import PLUGIN, ShellFixture


class RegistryTests(unittest.TestCase):
    def test_registration_is_atomic_for_the_entire_feature(self):
        for paths, handler in ((('/api/new', '/api/existing'), lambda request: None),
                               (('/api/new', '/api/new'), lambda request: None),
                               (('/api/new', '/not-an-api'), lambda request: None),
                               (('/api/new',), None)):
            with self.subTest(paths=paths, callable=callable(handler)):
                routes = Registry()
                routes.add('GET', '/api/existing', lambda request: {'kept': True})
                with self.assertRaises(ValueError):
                    routes.add('GET', paths, handler)
                self.assertIsNone(routes.lookup('GET', '/api/new'))
                self.assertEqual(routes.dispatch('GET', ApiRequest('/api/existing')), {'kept': True})

    def test_feature_owns_its_paths_and_duplicate_registration_is_rejected(self):
        routes = Registry()
        routes.add('GET', '/api/example', lambda request: request.one('text'))
        self.assertEqual(routes.dispatch('GET', ApiRequest('/api/example', {'text': ['kept']})), 'kept')
        with self.assertRaisesRegex(ValueError, 'Duplicate feature route'):
            routes.add('GET', '/api/example', lambda request: None)
        self.assertIsNone(routes.lookup('POST', '/api/example'))

    def test_only_admitted_routes_enter_model_work(self):
        events = []

        @contextmanager
        def admit():
            events.append('enter')
            try:
                yield
            finally:
                events.append('leave')

        routes = Registry(admit)
        routes.add('POST', '/api/example/read', lambda request: events.append('read'))
        routes.add('POST', '/api/example/start', lambda request: events.append('start'), model_work=True)
        routes.dispatch('POST', ApiRequest('/api/example/read'))
        routes.dispatch('POST', ApiRequest('/api/example/start'))
        self.assertEqual(events, ['read', 'enter', 'start', 'leave'])

    def test_local_namespace_policy_also_protects_unknown_paths(self):
        routes = Registry()
        routes.protect_local('/api/private')
        routes.add('POST', '/api/exact', lambda request: None, local_only=True)
        self.assertTrue(routes.local_only('/api/private/missing'))
        self.assertTrue(routes.local_only('/api/exact'))
        self.assertFalse(routes.local_only('/api/private-other'))
        self.assertFalse(routes.local_only('/api/exact/missing'))


class FeatureRegistrationHttpTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        poll = mock.patch.object(web.Service, '_poll_notices', return_value=None)
        poll.start()
        self.addCleanup(poll.stop)
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=lambda: [], claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, process_factory=self.process_factory, isolated_sources=True)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.opener = build_opener(ProxyHandler({}))

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

    def request(self, path, data=None, headers=None):
        request = Request(self.server.url + path,
            None if data is None else json.dumps(data).encode('utf-8'),
            {'X-Crew': '1', **(headers or {})})
        try:
            response = self.opener.open(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            content = response.read()
            if response.headers.get_content_type() == 'application/json':
                content = json.loads(content)
            return response.status, content, response.headers

    def test_registering_a_feature_needs_no_transport_or_service_dispatch_branch(self):
        routes = self.server.service.api
        routes.add('GET', '/api/example', lambda request: {'value': request.one('value')})
        routes.add('POST', '/api/example', lambda request: {'accepted': request.data['text']})
        self.assertEqual(self.request('/api/example?value=original')[:2], (200, {'value': 'original'}))
        self.assertEqual(self.request('/api/example', {'text': '保留原始输入'})[:2],
                         (200, {'accepted': '保留原始输入'}))
        self.assertEqual(self.request('/api/example/missing')[0], 404)
        self.assertEqual(self.request('/api/example/missing', {})[0], 404)

    def test_registered_work_is_refused_before_the_feature_has_any_side_effect(self):
        calls = []
        service = self.server.service
        service.api.add('POST', '/api/example/work', lambda request: calls.append('work'), model_work=True)
        service.api.add('POST', '/api/example/settings', lambda request: {'saved': True})
        self.assertTrue(service.chat.close_admission('module-test', 0))
        self.addCleanup(service.chat.open_admission, 'module-test')
        self.assertEqual(self.request('/api/example/work', {})[0], 409)
        self.assertEqual(calls, [])
        self.assertEqual(self.request('/api/example/settings', {})[:2], (200, {'saved': True}))

    def test_feature_security_and_file_response_use_the_same_real_transport(self):
        calls = []
        routes = self.server.service.api
        routes.add('POST', '/api/example/private', lambda request: calls.append(request.data), local_only=True)
        code, _, _ = self.request('/api/example/private', {'secret': 'kept'}, {'Origin': 'https://other.invalid'})
        self.assertEqual(code, 403)
        self.assertEqual(calls, [])
        routes.add('GET', '/api/example/report', lambda request: Content(
            b'<h1>Isolated</h1>', 'text/html; charset=utf-8', sandbox=True, interactive_report=True))
        code, content, headers = self.request('/api/example/report')
        self.assertEqual((code, content), (200, b'<h1>Isolated</h1>'))
        self.assertIn('sandbox allow-scripts', headers['Content-Security-Policy'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
