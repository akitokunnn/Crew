"""HTTP contracts for the route exceptions in SPLIT_BOUNDARIES §1.4.4."""
import tests
import base64
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode
from urllib.request import urlopen
from uuid import uuid4

from crew import board, goals, store, updater, web
import test_chat_http
import test_goal_http
from test_shell import NOW, PLUGIN, ShellFixture


class RecordingLock:
    def __init__(self, lock):
        self.acquire = Mock(wraps=lock.acquire)
        self.release = lock.release

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, tb):
        self.release()


class RouteDispatchContractTests(ShellFixture, unittest.TestCase):
    request = test_chat_http.ChatHttpTests.request

    def setUp(self):
        super().setUp()
        # As in the storage HTTP fixture, run the real handler without periodic
        # workers competing for the same admission gate and write lock.
        self.service = web.Service(
            self.home, PLUGIN, cwd=self.project, agents_provider=lambda: [],
            process_factory=self.process_factory, now=lambda: store.parse_iso(NOW),
            claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
            vscode_state_db=self.vscode_state_db, isolated_sources=True)
        self.addCleanup(self.service.close)
        server = ThreadingHTTPServer(('127.0.0.1', 0), web.Handler)
        server.service = self.service
        self.url = 'http://127.0.0.1:%d' % server.server_port
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01})
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)

    def post(self, path, body, *, work, locked, status=200):
        lock = RecordingLock(self.service.write_lock)
        with patch.object(self.service, 'write_lock', lock), patch.object(
                self.service.chat, 'work', wraps=self.service.chat.work) as admission:
            code, result, _ = self.request(path, body)
        self.assertEqual(code, status, (path, result))
        self.assertEqual(admission.called, work, path)
        self.assertEqual(lock.acquire.called, locked, path)
        return result

    def goal(self):
        return goals.create(self.conn, 'demo', 's3', 'Route check', 'Check the route', ['Checked'])

    def test_goals_agent_preview_and_status(self):
        goal = self.goal()
        path = '/api/goals/agent?project=demo'
        body = {'actor_session_id': 's3', 'data': {'goal_id': goal['id']}}
        status_body = {**body, 'action': 'status'}
        status = self.post(path, status_body, work=True, locked=True)
        self.assertEqual(status['goal']['id'], goal['id'])
        self.assertTrue(self.service.chat.close_admission('route-check', timeout=.1))
        self.addCleanup(self.service.chat.open_admission, 'route-check')
        refused = self.post(path, status_body, work=True, locked=False, status=409)
        self.assertIn('暂不接受新任务', refused['error'])
        preview = self.post(path, {
            **body, 'action': 'report.preview',
            'data': {**body['data'], 'document_html': '<p>Route check</p>'}},
            work=False, locked=False)
        self.assertFalse(preview['ok'])
        self.assertIn('请打开 Crew 桌面', preview['error'])
        self.assertEqual(self.processes, [])

    def test_report_folder_and_goal_action(self):
        goal = self.goal()
        report = goals.save_report(self.conn, goal['id'], test_goal_http.GoalHttpTests.report())
        with patch('sys.platform', 'win32'), patch('crew.native_folders.open_windows') as opened:
            folder = self.post('/api/goals/report/folder?project=demo', {
                'goal_id': goal['id'], 'report_id': report['id']}, work=True, locked=False)
        self.assertTrue(folder['ok'])
        self.assertTrue(folder['declared'])
        self.assertEqual(Path(folder['path']), self.project)
        opened.assert_called_once_with(folder['path'])
        result = self.post('/api/goals/action?project=demo', {
            'goal_id': goal['id'], 'action': 'pause', 'version': goal['version']},
            work=True, locked=True)
        self.assertEqual(result['goal']['id'], goal['id'])
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'paused')

    def test_runtime_prepare_and_session_rename(self):
        path = '/api/session/runtime/prepare?project=demo'
        invalid = self.post(path, {'session_id': 's1', 'runtime': 'invalid'},
                            work=False, locked=False, status=400)
        self.assertEqual(invalid['error'], '不支持的运行时: invalid')
        with patch.object(self.service.runtime_catalog, 'refresh', return_value=[
                {'id': 'codex', 'available': True, 'models': []}]):
            preview = self.post(path, {'session_id': 's1', 'runtime': 'codex'},
                                work=False, locked=True)
        self.assertTrue(preview['preview_id'])
        self.assertEqual(preview['to']['runtime'], 'codex')
        self.assertFalse(preview['starts_turn'])
        result = self.post('/api/session/rename?project=demo', {'session_id': 's1', 'name': 'renamed'},
                           work=False, locked=True)
        self.assertEqual(result['before']['crew_name'], 'one')
        self.assertEqual(board.session(self.conn, 's1')['crew_name'], 'renamed')
        self.assertEqual(self.processes, [])

    def test_admin_prepare_and_cancel_control_admission(self):
        instance = {'instance_id': self.service.instance_id}
        update_lock = updater.update_lock(self.home)
        self.assertTrue(update_lock.acquire())
        self.addCleanup(update_lock.release)
        for path, locked in (('/api/admin/update/prepare', False),
                             ('/api/admin/connection/prepare', True)):
            with self.subTest(path=path):
                result = self.post(path, instance, work=False, locked=locked)
                self.assertEqual(result['status'], 'ready')
                self.assertEqual(result['instance_id'], instance['instance_id'])
                self.assertEqual(self.service.chat.paused(), result['gate_id'])
                cancelled = self.post('/api/admin/update/cancel', {**instance, 'gate_id': result['gate_id']},
                                      work=False, locked=False)
                self.assertEqual(cancelled, {'ok': True})
                self.assertIsNone(self.service.chat.paused())

    def test_admin_routes_reject_stale_instances_before_locking(self):
        for path, error in (
                ('/api/admin/update/prepare', '后台实例已变化，请重新检查后再更新'),
                ('/api/admin/update/cancel', '后台实例已变化，更新准备已失效'),
                ('/api/admin/connection/prepare', '后台实例已变化，请重新检查连接设置')):
            with self.subTest(path=path):
                result = self.post(path, {'instance_id': 'stale'}, work=False, locked=False, status=409)
                self.assertEqual(result['error'], error)

    def test_channel_checks_and_uses_body_project(self):
        board.init_project(self.conn, 'other', self.root / 'other', now=NOW)
        body = {'project': 'other', 'body': 'rejected'}
        error = self.post('/api/channel?project=demo', body, work=False, locked=True, status=400)
        self.assertEqual(error['error'], '请求正文和地址中的项目不一致')
        for path, message in (('/api/channel?project=other', 'matching'), ('/api/channel', 'body only')):
            result = self.post(path, {**body, 'body': message}, work=False, locked=True)
            self.assertEqual(result['message']['body'], message)
        for project, expected in (('demo', []), ('other', ['matching', 'body only'])):
            code, result, _ = self.request('/api/channel?project=' + project)
            self.assertEqual(code, 200, result)
            self.assertCountEqual([row['body'] for row in result['messages']], expected)

    def test_roster_move_by_session_and_by_name(self):
        path = '/api/roster/move?project=demo'
        board.ensure_spawn_group(self.conn, 'demo', 'next', now=NOW)
        detail = self.post(path, {'session_id': 's1', 'to': 'next'}, work=False, locked=True)
        self.assertTrue(detail['ok'])
        self.assertEqual(detail['before']['group_name'], 'lead')
        self.assertEqual(detail['session']['session_id'], 's1')
        self.assertEqual(detail['session']['group_name'], 'next')
        result = self.post(path, {'name': 'one', 'to': 'lead'}, work=False, locked=True)
        self.assertEqual(result, {'ok': True})
        self.assertEqual(board.session(self.conn, 's1')['group_name'], 'lead')

    def test_unknown_paths(self):
        path = '/api/goals/unregistered?project=demo'
        error = self.post(path, {}, work=False, locked=False, status=404)
        self.assertEqual(error['error'], '接口不存在')
        code, error, _ = self.request(path)
        self.assertEqual((code, error['error']), (404, '页面不存在'))

    def stream(self, path, mime):
        code, content, headers = self.request(path, raw=True)
        self.assertEqual(code, 200, (path, content[:200]))
        self.assertEqual(headers['Content-Type'], mime)
        self.assertEqual(headers['Content-Length'], str(len(content)))
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        return content, headers

    def test_file_download_and_inline_content(self):
        filename = '交付.png'
        data = b'\x89PNG\r\n\x1a\n\x00\xff'
        (self.project / filename).write_bytes(data)
        query = '?' + urlencode({'project': 'demo', 'path': filename, 'raw': 1})
        download, headers = self.stream('/api/files/download' + query, 'application/octet-stream')
        self.assertEqual(download, data)
        self.assertEqual(headers['Content-Disposition'],
                         'attachment; filename="__.png"; filename*=UTF-8\'\'%E4%BA%A4%E4%BB%98.png')
        self.assertIn('sandbox;', headers['Content-Security-Policy'])
        content, headers = self.stream('/api/files/content' + query, 'image/png')
        self.assertEqual(content, data)
        self.assertIsNone(headers['Content-Disposition'])
        self.assertIn('sandbox;', headers['Content-Security-Policy'])

    def test_report_html_and_json(self):
        goal = self.goal()
        report = goals.save_report(self.conn, goal['id'], test_goal_http.GoalHttpTests.report())
        path = '/api/goals/report?' + urlencode({
            'goal_id': goal['id'], 'report_id': report['id'], 'project': 'demo'})
        content, headers = self.stream(path, 'text/html; charset=utf-8')
        self.assertEqual(content, report['html'].encode('utf-8'))
        self.assertIn('sandbox allow-scripts;', headers['Content-Security-Policy'])
        code, value, headers = self.request(path + '&format=json')
        self.assertEqual(code, 200, value)
        self.assertEqual(headers.get_content_type(), 'application/json')
        self.assertEqual(value['id'], report['id'])
        self.assertNotIn('html', value)

    def test_workspace_artifact_blob_and_package_streams(self):
        repo = self.service.storage.repository
        ws = repo.create_workspace('Downloads')['id']
        artifact, version = str(uuid4()), str(uuid4())
        with repo.transaction() as conn:
            document = repo.register_blob(ws, repo.objects.put_bytes(ws, b'notes'), 'text/plain', conn=conn)
            image = repo.register_blob(ws, repo.objects.put_bytes(ws, b'photo'), 'image/png', conn=conn)
            conn.execute('INSERT INTO artifacts(id,workspace_id,created_at,updated_at,name,kind) '
                         'VALUES(?,?,?,?,?,?)', (artifact, ws, NOW, NOW, 'notes.txt', 'document'))
            conn.execute('INSERT INTO artifact_versions(id,workspace_id,created_at,artifact_id,version_no,'
                         'blob_id,display_filename,mime_type) VALUES(?,?,?,?,?,?,?,?)',
                         (version, ws, NOW, artifact, 1, document['id'], 'notes.txt', 'text/plain'))
        for route, params, mime, data in (
                ('artifact', {'version_id': version}, 'text/plain', b'notes'),
                ('blob', {'blob_id': image['id']}, 'image/png', b'photo')):
            with self.subTest(route=route):
                path = '/api/workspace-data/' + route + '?' + urlencode({'workspace_id': ws, **params})
                content, headers = self.stream(path, mime)
                self.assertEqual(content, data)
                self.assertIn('sandbox;', headers['Content-Security-Policy'])
                self.assertIsNone(headers['Content-Disposition'])
        code, package, _ = self.request('/api/workspace-data/export', {'workspace_id': ws})
        self.assertEqual(code, 200, package)
        content, headers = self.stream('/api/workspace-data/download?id=' + package['download_id'],
                                       'application/zip')
        self.assertEqual(content, Path(package['path']).read_bytes())
        self.assertIn('attachment; filename="workspace.crewpack"', headers['Content-Disposition'])

    def test_custom_sound_stream(self):
        wav = b'RIFF\x24\x00\x00\x00WAVEfmt ' + bytes(28)
        code, value, _ = self.request('/api/sounds/custom', {
            'name': 'bell', 'filename': 'bell.wav', 'data': base64.b64encode(wav).decode('ascii')})
        self.assertEqual(code, 200, value)
        content, headers = self.stream(value['preset']['url'], 'audio/wav')
        self.assertEqual(content, wav)
        self.assertIsNone(headers['Content-Disposition'])

    def test_chat_events_and_history(self):
        self.service.chat.journal('s1').message('event-id', 'route event')
        query = '?session_id=s1&project=demo'
        code, history, headers = self.request('/api/chat/history' + query)
        self.assertEqual(code, 200, history)
        self.assertEqual(headers.get_content_type(), 'application/json')
        event = history['events'][-1]
        self.assertEqual(event['text'], 'route event')
        with urlopen(self.url + '/api/chat/events' + query, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers['Content-Type'], 'text/event-stream; charset=utf-8')
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(response.headers['Connection'], 'close')
            self.assertEqual(response.headers['X-Crew-Latest-Seq'], str(event['seq']))
            self.assertEqual(response.headers['X-Crew-Execution-Id'], '')
            self.assertEqual(response.readline(), ('id: %s\n' % event['seq']).encode())
            line = response.readline()
            self.assertTrue(line.startswith(b'data: '))
            self.assertEqual(json.loads(line[6:])['id'], 'event-id')
