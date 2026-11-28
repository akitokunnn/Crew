"""Exercise notification collection with a real database and long-lived HTTP streams."""
import tests
from datetime import timedelta
import http.client
from http.server import ThreadingHTTPServer
import json
import threading
import time
import unittest
from unittest.mock import patch

from crew import board, device_notifications, goals, remote_http, store, todo, web, workspace_settings
import test_goal_http as goal_helpers
from test_shell import NOW, PLUGIN, ShellFixture


EVENTS = '/api/notifications/events'


class DeviceNotificationTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.clock = store.parse_iso(NOW)
        self.service = web.Service(self.home, PLUGIN, agents_provider=lambda: [], isolated_sources=True,
                                   claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
                                   process_factory=self.process_factory, now=lambda: self.clock,
                                   vscode_state_db=self.vscode_state_db)
        self.addCleanup(self.service.close)
        self.controller = self.service.remote_access

    def rows(self):
        return board._rows(self.conn, 'SELECT * FROM device_notifications ORDER BY seq')

    def add_todo(self, title):
        return todo.add(self.conn, 's1', title, '操作步骤', '确认方法', now=NOW)

    def start_lan(self):
        return self.controller.action({'action': 'start', 'bind': '127.0.0.1', 'port': 0})

    def wait_for_rows(self, count):
        deadline = time.monotonic() + 5
        while len(self.rows()) < count and time.monotonic() < deadline:
            time.sleep(.02)
        rows = self.rows()
        self.assertEqual(len(rows), count, rows)
        return rows

    def test_collects_reminder_kinds_without_channels_and_deduplicates(self):
        with patch.object(device_notifications, 'COLLECT_SECONDS', .04):
            self.service.inbox.set_away(True)
            self.assertTrue(self.service.inbox.status()['away'])
            self.assertTrue(all(inbox.credentials is None for inbox in self.service.connections.inboxes.values()))
            workspace_settings.apply_update(self.conn, 'demo', '示例工作区', None, 0, now=NOW)
            self.start_lan()
            decision = board.ask(self.conn, 's1', '哪一步？\nhttp://localhost:123/private', now=NOW)
            task = self.add_todo('手动复核')
            goal = goals.create(self.conn, 'demo', 's3', '交付明细' * 22, '核对报告', ['结果'], now=NOW)
            goals.transition(self.conn, goal['id'], 'running', now=NOW)
            report = goals.save_report(self.conn, goal['id'], goal_helpers.GoalHttpTests.report(), now=NOW)
            goals.request_finish(self.conn, goal['id'], report['id'], actor='s3', now=NOW)
            self.assertTrue(goals.finalize_finish(self.conn, goal['id'], now=NOW)['finalized'])
            self.service.chat.journal('s1').emit('error', text='模拟出错')
            self.conn.execute('INSERT INTO codex_runs(run_id,task_file,workdir,started_at,ended_at,exit_code,crew_session_id) '
                              'VALUES (?,?,?,?,?,?,?)', ('failed', '复核.md', str(self.project), NOW, NOW, 1, 's3'))
            rows = {row['kind']: row for row in self.wait_for_rows(5)}
            self.assertEqual(set(rows), {'decision', 'todo', 'goal_report', 'session_error', 'codex_failed'})
            self.assertEqual(rows['decision']['item_id'], f'decision:{decision}')
            self.assertNotIn('localhost', rows['decision']['title'])
            self.assertIn('本机地址', rows['decision']['title'])
            self.assertEqual(rows['decision']['url'], '/?project=demo#all')
            self.assertEqual(rows['todo']['item_id'], f"todo:{task['id']}:created")
            self.assertEqual(rows['todo']['url'], '/?project=demo#todos')
            self.assertEqual(rows['session_error']['url'], '/?project=demo#history=s1')
            self.assertEqual(rows['codex_failed']['url'], '/?project=demo#leader=s3')
            self.assertEqual(rows['goal_report']['url'],
                             f"/api/op/goal.report.view?project=demo&goal_id={goal['id']}&report_id={report['id']}")
            self.assertTrue(rows['goal_report']['title'].endswith('待你验收'))
            for row in rows.values():
                self.assertEqual(row['body'], '示例工作区 · lead')
                self.assertLessEqual(len(row['title']), 60)
                self.assertNotIn('\n', row['title'])
            self.controller.shutdown()
        device_notifications.collect(self.service, self.conn, self.clock, seen=set())
        self.assertEqual(len(self.rows()), 5)

    def test_start_stop_and_saved_start_baseline_current_items(self):
        with patch.object(device_notifications, 'COLLECT_SECONDS', .04):
            self.add_todo('局域网开启前')
            self.start_lan()
            self.assertEqual(self.rows(), [])
            first = self.add_todo('本次开启后')
            self.assertEqual(self.wait_for_rows(1)[0]['item_id'], f"todo:{first['id']}:created")
            worker = self.controller.device_notifications.worker
            self.controller.action({'action': 'stop'})
            self.assertFalse(worker.is_alive())
            self.add_todo('访问关闭期间')
            self.start_lan()
            self.controller.shutdown()
            self.add_todo('后台重启期间')
            self.assertTrue(self.controller.start_saved()['enabled'])
            second = self.add_todo('恢复监听后')
            self.assertEqual(self.wait_for_rows(2)[1]['item_id'], f"todo:{second['id']}:created")

    def test_retention_keeps_recent_in_age_or_count_and_never_reinserts_removed_id(self):
        seen = set()
        old = self.add_todo('很久以前的待办')
        old_at = self.clock - timedelta(days=8)
        device_notifications.collect(self.service, self.conn, old_at, seen=seen)
        self.insert_row('recent-low', (self.clock - timedelta(days=7)).isoformat())
        for index in range(500):
            self.insert_row(f'old-{index}', old_at.isoformat())
        self.add_todo('今天的第一条')
        device_notifications.collect(self.service, self.conn, self.clock, seen=seen)
        self.add_todo('今天的第二条')
        device_notifications.collect(self.service, self.conn, self.clock, seen=seen)
        self.start_lan()
        self.controller.shutdown()
        self.add_todo('下一次监听启动后的新提醒')
        device_notifications.collect(self.service, self.conn, self.clock, seen=self.controller.device_notifications.seen)
        ids = {row['item_id'] for row in self.rows()}
        self.assertEqual(len(ids), 501)
        self.assertIn('recent-low', ids)
        self.assertNotIn('old-0', ids)
        self.assertIn('old-499', ids)
        self.assertNotIn(f"todo:{old['id']}:created", ids)

    def test_failed_scan_is_logged_and_retried_by_running_collector(self):
        state = self.service.state
        failed = threading.Event()

        def fail_once(name):
            if not failed.is_set():
                failed.set()
                raise OSError('test: 读取暂时失败')
            return state(name)

        with patch.object(device_notifications, 'COLLECT_SECONDS', .04), self.assertLogs(device_notifications.LOG, 'ERROR') as logs:
            self.start_lan()
            with patch.object(self.service, 'state', side_effect=fail_once):
                task = self.add_todo('采集失败后仍收到')
                self.assertEqual(self.wait_for_rows(1)[0]['item_id'], f"todo:{task['id']}:created")
                self.assertTrue(failed.is_set())
        self.assertIn('OSError: test: 读取暂时失败', '\n'.join(logs.output))

    def insert_row(self, identity, at=NOW):
        return self.conn.execute("INSERT INTO device_notifications(item_id,kind,project,title,body,url,created_at) "
                                 "VALUES (?,'todo','demo','复核','示例工作区 · lead','/?project=demo#todos',?)",
                                 (identity, at)).lastrowid

    def http(self, port, path, *, data=None, headers=None, timeout=4):
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
        self.addCleanup(conn.close)
        if data is not None:
            headers = {'Content-Type': 'application/json', 'X-Crew': '1', **headers}
        conn.request('GET' if data is None else 'POST', path,
                     body=None if data is None else json.dumps(data).encode(), headers=headers or {})
        response = conn.getresponse()
        self.addCleanup(response.close)
        return response

    def local_port(self):
        class LocalServer(ThreadingHTTPServer):
            server_bind = remote_http.bind_without_fqdn

        server = LocalServer(('127.0.0.1', 0), web.Handler)
        server.service = self.service
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        thread.start()

        def close():
            self.service.stop.set()
            server.shutdown()
            server.server_close()
            thread.join()

        self.addCleanup(close)
        return server.server_port

    def event(self, response):
        line = response.readline()
        seq = int(line[4:]) if line.startswith(b'id: ') else None
        if seq is not None:
            line = response.readline()
        self.assertTrue(line.startswith(b'data: '), line)
        self.assertEqual(response.readline(), b'\n')
        return seq, json.loads(line[6:])

    def test_sse_first_connection_ready_then_only_new_notification_and_heartbeat(self):
        port = self.local_port()
        first = self.insert_row('already-present')
        response = self.http(port, EVENTS, timeout=18)
        self.assertEqual(response.getheader('Content-Type'), 'text/event-stream; charset=utf-8')
        self.assertEqual(self.event(response), (None, {'type': 'ready', 'seq': first}))
        seq = self.insert_row('just-added')
        self.assertEqual(self.event(response), (seq, {
            'seq': seq, 'type': 'notification', 'item_id': 'just-added', 'kind': 'todo', 'project': 'demo',
            'title': '复核', 'body': '示例工作区 · lead', 'url': '/?project=demo#todos', 'created_at': NOW}))
        self.assertEqual(response.readline(), b': heartbeat\n')
        self.assertEqual(response.readline(), b'\n')

    def test_sse_resume_gap_boundary_max_cursor_and_local_context(self):
        port = self.local_port()
        empty = self.http(port, EVENTS)
        self.assertEqual(self.event(empty), (None, {'type': 'ready', 'seq': 0}))
        removed = self.insert_row('trimmed')
        oldest, latest = (self.insert_row(identity) for identity in ('oldest', 'latest'))
        self.conn.execute('DELETE FROM device_notifications WHERE seq=?', (removed,))
        gap = self.http(port, EVENTS + '?after=0')
        self.assertEqual(self.event(gap), (None, {'type': 'gap', 'oldest': oldest}))
        self.assertEqual([self.event(gap)[0] for _ in range(2)], [oldest, latest])
        for query, headers, first in (('after=0', {'Last-Event-ID': str(oldest)}, latest),
                                       (f'after={oldest}', {'Last-Event-ID': '0'}, latest),
                                       (f'after={oldest - 1}', {}, oldest)):
            with self.subTest(query=query, headers=headers):
                response = self.http(port, EVENTS + '?' + query, headers=headers)
                self.assertEqual(self.event(response)[0], first)
        for query in ('after=', 'after=-1', 'after=9223372036854775808'):
            self.assertEqual(self.http(port, EVENTS + '?' + query).status, 400)
        context = json.loads(self.http(port, '/api/access/context').read())
        self.assertFalse(context['remote'])
        self.assertEqual({key: context[key] for key in ('host_name', 'instance_id')}, self.controller.host_context())

    def test_sse_browser_revocation_and_desktop_listener_stop_end_idle_streams(self):
        port = self.start_lan()['port']
        self.assertEqual(self.http(port, EVENTS).status, 401)
        for kind in ('browser', 'desktop'):
            pair = self.controller.action({'action': 'pair'})
            result = self.http(port, '/remote/login', data={'code': pair['code'], 'kind': kind},
                               headers={'Origin': self.controller.listener.url})
            self.assertEqual(result.status, 200)
            value = json.loads(result.read())
            headers = ({'Cookie': result.getheader('Set-Cookie').split(';', 1)[0]} if kind == 'browser'
                       else {'Authorization': 'Bearer ' + value['token']})
            stream = self.http(port, EVENTS, headers=headers)
            self.assertEqual(self.event(stream), (None, {'type': 'ready', 'seq': 0}))
            if kind == 'browser':
                context = json.loads(self.http(port, '/api/access/context', headers=headers).read())
                self.controller.action({'action': 'revoke', 'device_id': context['device_id']})
            else:
                self.controller.action({'action': 'stop'})
            self.assertEqual(stream.readline(), b'')
