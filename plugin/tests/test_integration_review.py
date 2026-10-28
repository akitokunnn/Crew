"""Independent HTTP regressions for task control and update admission boundaries."""
import threading
import unittest
from unittest import mock

import tests
import test_chat_http as helpers
from test_shell import NOW, PLUGIN, ShellFixture
from crew import goals, store, updater, web


class IntegrationReviewTests(ShellFixture, unittest.TestCase):
    close_server = helpers.ChatHttpTests.close_server
    request = helpers.ChatHttpTests.request
    finish_turn = helpers.ChatHttpTests.finish_turn

    def setUp(self):
        super().setUp()
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=mock.Mock(return_value=[]), claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory,
            isolated_sources=True)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = self.server.url

    def running_task(self):
        code, value, _ = self.request('/api/tasks/create', {'project': 'demo', 'text': 'Finite isolated review work',
            'acceptance': ['Record actual result'], 'leader_session_id': 's3', 'request_id': 'review-create'})
        self.assertEqual(code, 200, value)
        task = value['task']
        code, started, _ = self.request('/api/tasks/start', {'task_id': task['id'], 'version': task['version'], 'request_id': 'review-start'})
        self.assertEqual(code, 200, started)
        return started

    def test_task_stop_remains_available_after_an_idle_service_enters_the_real_update_gate(self):
        started = self.running_task()
        self.finish_turn(started['receipt']['execution_id'], self.processes[-1])
        task = started['task']
        lock = updater.update_lock(self.home)
        self.assertTrue(lock.acquire())
        gate = None
        try:
            code, ready, _ = self.request('/api/admin/update/prepare', {'instance_id': self.server.service.instance_id})
            self.assertEqual(code, 200, ready)
            self.assertEqual(ready['status'], 'ready', ready)
            gate = ready['gate_id']
            code, stopped, _ = self.request('/api/tasks/stop', {'task_id': task['id'], 'version': task['version'], 'request_id': 'stop-during-update'})
            self.assertEqual(code, 200, stopped)
            self.assertEqual(stopped['task']['state'], 'stopped')
            self.assertTrue(goals.get(self.conn, task['id'])['manual_pause'])
        finally:
            if gate is not None:
                self.server.service.chat.open_admission(gate)
            lock.release()

    def test_rejected_second_start_does_not_label_a_known_precondition_as_unknown_native_delivery(self):
        started = self.running_task()
        task = started['task']
        before = goals.get(self.conn, task['id'])
        count = len(self.processes)
        code, rejected, _ = self.request('/api/tasks/start', {'task_id': task['id'], 'version': task['version'], 'request_id': 'known-second-start'})
        self.assertEqual(code, 409, rejected)
        self.assertEqual(len(self.processes), count)
        self.assertEqual(goals.get(self.conn, task['id'])['reason'], before['reason'])
        receipt = self.conn.execute('SELECT status FROM task_requests WHERE request_id=?', ('known-second-start',)).fetchone()
        self.assertTrue(receipt is None or receipt['status'] != 'unknown')

    def test_uploaded_image_message_id_cannot_write_outside_its_general_or_task_conversation(self):
        for scope in ('general', 'task'):
            with self.subTest(scope=scope):
                if scope == 'general':
                    self.server.service.chat.journal('s3').set_status('idle')
                    data = {'session_id': 's3'}
                else:
                    started = self.running_task()
                    data = {'session_id': started['task']['leader_session_id'], 'goal_id': started['task']['id']}
                data.update(text='Inspect isolated fixture image', message_id='../audit-outside-' + scope,
                            images=[helpers.ChatHttpTests.image(data=b'isolated non-sensitive image fixture')])
                code, result, _ = self.request('/api/chat/send?project=demo', data)
                self.assertIn(code, (200, 400), result)
                escaped = self.home / 'chat' / 'files' / ('audit-outside-' + scope + '-1.png')
                self.assertFalse(escaped.exists(), 'Caller-controlled message_id wrote outside its conversation: ' + str(escaped))


if __name__ == '__main__':
    unittest.main()
