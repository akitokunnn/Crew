"""Live HTTP and SQLite authorization for synthetic report captures, no AI."""
import base64
from pathlib import Path
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import tests
from crew import board, goals, remote_routes, report_preview, store, web
import test_chat_http
from test_report_preview import png
from test_shell import ShellFixture, PLUGIN, NOW


class ReportPreviewHttpTests(ShellFixture, unittest.TestCase):
    request = test_chat_http.ChatHttpTests.request
    close_server = test_chat_http.ChatHttpTests.close_server

    def setUp(self):
        super().setUp()
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=lambda: [], claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = self.server.url
        self.server.service.report_previews.poll_seconds = .03
        self.server.service.report_previews.timeout = 2
        self.goal = goals.create(self.conn, 'demo', 's3', 'Synthetic report', 'Render a synthetic report only.', ['Readable'])

    def post_action(self, data, *, actor='s3', execution=None):
        body = {'action': 'report.preview', 'actor_session_id': actor, 'data': data}
        if execution:
            body['execution_id'] = execution
        return self.request('/api/goals/agent?project=demo', body)

    def capture(self, data, *, actor='s3', execution=None):
        result = []
        thread = threading.Thread(target=lambda: result.append(self.post_action(data, actor=actor, execution=execution)))
        thread.start()
        self.addCleanup(lambda: thread.join(3))
        return thread, result

    def test_real_http_draft_capture_does_not_lock_other_work_or_create_ai_turns(self):
        code, worker, _ = self.request(report_preview.WORKER_PREFIX + 'register', {})
        self.assertEqual(code, 200, worker)
        self.assertTrue(self.server.service.chat.close_admission('preview-read-only', timeout=.1))
        self.addCleanup(lambda: self.server.service.chat.open_admission('preview-read-only'))
        thread, result = self.capture({'goal_id': self.goal['id'], 'document_html': '<h1>Synthetic mobile report</h1>', 'viewport': 'mobile'})
        code, response, _ = self.request(report_preview.WORKER_PREFIX + 'next', worker)
        self.assertEqual(code, 200, response)
        job = response['job']
        self.assertTrue(self.server.service.write_lock.acquire(timeout=.2))
        self.server.service.write_lock.release()
        self.assertTrue(self.server.service.chat.lock.acquire(timeout=.2))
        self.server.service.chat.lock.release()
        self.assertEqual(self.request('/api/state?project=demo')[0], 200)
        code, response, _ = self.request(report_preview.WORKER_PREFIX + 'result', {
            **worker, 'job_id': job['id'], 'png_base64': base64.b64encode(png(390, 844)).decode('ascii'),
            'console_errors': ['Synthetic warning retained']})
        self.assertEqual(code, 200, response)
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0][0], 200, result)
        preview = result[0][1]['preview']
        self.assertEqual(Path(preview['path']).read_bytes(), png(390, 844))
        self.assertEqual(preview['console_errors'], ['Synthetic warning retained'])
        self.assertEqual(self.processes, [])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_reports').fetchone()[0], 0)
        self.assertEqual(goals.get(self.conn, self.goal['id'])['status'], 'queued')

    def test_permissions_cross_goal_reports_and_unsafe_input_are_rejected_before_queueing(self):
        payload = {'goal_id': self.goal['id'], 'document_html': '<p>Synthetic only</p>'}
        code, response, _ = self.post_action(payload, actor='s2')
        self.assertEqual(code, 400, response)
        for extra in ({'path': 'other.html'}, {'url': 'https://example.invalid'}, {'script': 'anything'},
                      {'report_id': 'another-report'}, {'viewport': 'arbitrary'}):
            code, response, _ = self.post_action({**payload, **extra})
            self.assertEqual(code, 400, response)
        other = goals.create(self.conn, 'demo', 's3', 'Other synthetic goal', 'A separate report.', ['Readable'])
        execution = goals.create_execution(self.conn, self.goal['id'], 's3', runtime='codex')
        code, response, _ = self.post_action({**payload, 'goal_id': other['id']}, execution=execution['id'])
        self.assertEqual(code, 400, response)
        self.assertIn('当前执行目标', response['error'])
        self.assertFalse((self.home / 'report-previews').exists())
        self.assertEqual(self.processes, [])

    def test_saved_report_scope_and_unavailable_worker_are_explicit(self):
        report = {'outcome': 'progress', 'summary': 'Synthetic intermediate report', 'delivered': [], 'unresolved': [],
            'acceptance': [{'criterion_id': self.goal['requirements']['acceptance'][0]['id'], 'status': 'unverified', 'evidence': []}],
            'examples': [{'kind': 'illustrative', 'input': 'Synthetic', 'expected': 'Readable', 'actual': 'Not checked', 'conclusion': 'Pending'}],
            'document_html': '<h1>Saved synthetic report</h1>', 'artifacts': [], 'tradeoffs': [], 'follow_up': 'Inspect',
            'investment': {'elapsed_seconds': None, 'usage': None, 'roles': []}}
        saved = goals.save_report(self.conn, self.goal['id'], report)
        code, response, _ = self.post_action({'goal_id': self.goal['id'], 'report_id': saved['id']})
        self.assertEqual(code, 200, response)
        self.assertFalse(response['ok'])
        self.assertIn('打开 Crew 桌面', response['error'])
        other = goals.create(self.conn, 'demo', 's3', 'Other task', 'Other report', ['Readable'])
        code, response, _ = self.post_action({'goal_id': other['id'], 'report_id': saved['id']})
        self.assertEqual(code, 400, response)
        self.assertIn('报告不属于', response['error'])

    def test_worker_endpoints_are_local_authenticated_and_close_pending_capture(self):
        for route in report_preview.WORKER_PATHS:
            self.assertFalse(remote_routes.permitted('POST', route))
        request = Request(self.url + report_preview.WORKER_PREFIX + 'register', b'{}',
                          {'X-Crew': '1', 'Origin': 'https://different-host.invalid'})
        with self.assertRaises(HTTPError) as rejected:
            urlopen(request, timeout=2)
        self.assertEqual(rejected.exception.code, 403)
        rejected.exception.close()
        code, worker, _ = self.request(report_preview.WORKER_PREFIX + 'register', {})
        self.assertEqual(code, 200)
        code, _, _ = self.request(report_preview.WORKER_PREFIX + 'next', {**worker, 'token': 'wrong'})
        self.assertEqual(code, 409)
        thread, result = self.capture({'goal_id': self.goal['id'], 'document_html': '<p>Pending synthetic report</p>'})
        self.assertIsNotNone(self.request(report_preview.WORKER_PREFIX + 'next', worker)[1]['job'])
        code, _, _ = self.request(report_preview.WORKER_PREFIX + 'close', worker)
        self.assertEqual(code, 200)
        thread.join(3)
        self.assertIn('桌面已关闭', result[0][1]['error'])
        self.assertFalse((self.home / 'report-previews').exists())
