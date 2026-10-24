"""Host integration: temporary Crew data, fake runtimes and ephemeral HTTP only."""
import tests
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from crew import board, chat, hooks, messaging, runtimes, store, todo, web
from test_shell import NOW, PLUGIN, ShellFixture


class FakeCodex:
    runtime = 'codex'

    def __init__(self, home, plugin_root, session, spec, options, journal, router, **kwargs):
        self.process = mock.Mock(pid=8123)
        self.process.poll.return_value = None
        self.journal, self.applied = journal, dict(options)
        self.context, self.pending_compact = None, False
        self.input_lock = threading.RLock()
        self.reader_done, self.reader = threading.Event(), mock.Mock()
        self.calls = []

    def send(self, text, identity, **kwargs):
        self.calls.append(('send', text))
        self.journal.message(identity, kwargs.get('display_text') or text,
                             source=kwargs.get('source', 'human'), sender=kwargs.get('sender'))
        self.journal.set_status('working')
        return identity

    def set_options(self, options):
        self.calls.append(('options', options))
        self.applied = dict(options)

    def interrupt(self):
        self.calls.append(('interrupt', None))
        self.journal.set_status('idle')

    def close(self):
        self.process.poll.return_value = 0
        self.reader_done.set()


class ManagedRuntimeTests(ShellFixture, unittest.TestCase):
    def test_mixed_wake_routes_reply_by_crew_identity_without_claude_address(self):
        runtimes.set(self.conn, 's3', 'codex', native_id='native-leader')
        self.conn.execute('UPDATE sessions SET cc_name=NULL WHERE session_id=?', ('s3',))
        with mock.patch.object(self.manager, '_resume', return_value=False) as resume:
            self.manager.wake(self.conn, board.session(self.conn, 's1'), '接着验收', caller_session_id='s3')
        self.assertIn('回复 leader 用 crew send lead', resume.call_args.args[2])
        notice = self.manager.wake(self.conn, board.session(self.conn, 's3'), '需要协调', caller_session_id='s1')
        self.assertIn('crew send one', board.pending_notices(self.conn, 's3')[0]['text'])
        self.assertTrue(notice['ok'])

    def test_codex_only_recovery_keeps_unknown_claude_processes_unlaunched(self):
        runtimes.set(self.conn, 's1', 'codex', native_id='native-one')
        self.manager.journal('s1').set_status('working')
        self.manager.journal('s2').set_status('working')
        with mock.patch.object(self.manager, '_resume') as resume:
            self.manager.auto_resume(self.conn, [], runtime='codex')
        self.assertEqual([call.args[1]['session_id'] for call in resume.call_args_list], ['s1'])
        self.assertIn('s2', self.manager.auto_candidates)

    def test_claude_hook_entrypoint_uses_its_explicit_home_environment(self):
        (self.home / 'blacklist.txt').write_text('Bash :: ISOLATED_HOME_PROBE :: bound-to-temp-home\n', encoding='utf-8')
        payload = {'session_id': 's1', 'cwd': str(self.project), 'hook_event_name': 'PreToolUse',
                   'tool_name': 'Bash', 'tool_input': {'command': 'echo ISOLATED_HOME_PROBE'}}
        result = subprocess.run([sys.executable, str(PLUGIN / 'crew_hook.py'), 'guard'],
            input=json.dumps(payload), capture_output=True, text=True, encoding='utf-8', timeout=10,
            env={**os.environ, 'CREW_HOME': str(self.home)})
        self.assertEqual(result.returncode, 2)
        self.assertIn('bound-to-temp-home', result.stderr)

    def test_codex_creation_and_controls_use_codex_driver(self):
        with mock.patch('crew.codex_runtime.CodexRunning', FakeCodex):
            result = self.manager.start(self.conn, 'demo', 'codex-chief', '验证独立目标', role='leader', runtime='codex')
        sid = result['session_id']
        current = self.manager.processes[sid]
        self.assertEqual(runtimes.get(self.conn, sid)['runtime'], 'codex')
        self.assertEqual(current.calls[0], ('send', '验证独立目标'))
        self.manager.set_options(self.conn, sid, {'model': 'codex-available-model', 'effort': 'high'})
        self.assertEqual(current.calls[-1][0], 'options')
        self.manager.interrupt(sid)
        self.assertEqual(current.calls[-1][0], 'interrupt')
        self.manager.stop_managed(sid)
        self.assertFalse(self.manager.running(sid))

    def test_peer_delivery_is_queued_while_busy_then_delivered_once(self):
        current = FakeCodex(self.home, PLUGIN, {}, {}, {}, self.manager.journal('s1'), None)
        self.manager.processes['s1'] = current
        current.journal.set_status('working')
        receipt = self.manager.send_message('s3', 's1', '继续已批准的任务', message_id='stable-call')
        again = self.manager.send_message('s3', 's1', '继续已批准的任务', message_id='stable-call')
        self.assertEqual(receipt['message_id'], again['message_id'])
        self.manager.deliver_messages()
        self.assertEqual(current.calls, [])
        current.journal.set_status('idle')
        self.manager.deliver_messages()
        self.assertEqual(len(current.calls), 1)
        self.assertIn('不是用户本人授权', current.calls[0][1])
        self.assertEqual(messaging.pending(self.conn, 's1'), [])
        self.manager.deliver_messages()
        self.assertEqual(len(current.calls), 1)

    def test_claude_hook_receives_peer_mail_with_trusted_crew_sender(self):
        (self.home / 'blacklist.txt').write_text('# isolated\n', encoding='utf-8')
        messaging.queue(self.conn, 's3', 's1', '来自另一个执行工具的信息')
        code, result = hooks.handle('guard', {'session_id': 's1', 'cwd': str(self.project),
                                            'tool_name': 'Read', 'tool_input': {'file_path': str(self.project / 'file.py')}},
                                    self.home, PLUGIN, {})
        self.assertEqual(code, 0)
        text = result['hookSpecificOutput']['additionalContext']
        self.assertIn('来自「lead」', text)
        self.assertIn('不是用户本人授权', text)
        self.assertEqual(messaging.pending(self.conn, 's1'), [])


class RuntimeFeatureHttpTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        for method in ('_poll_agents', '_poll_notices', '_poll_git_ci'):
            patcher = mock.patch.object(web.Service, method, return_value=None)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=lambda: [], claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=store.parse_iso(NOW), process_factory=self.process_factory,
            notification_client=mock.Mock(), lock_status=lambda: False, vscode_state_db=self.vscode_state_db)
        self.server.service.refresh_agents()
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.opener = build_opener(ProxyHandler({}))

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def request(self, path, data=None):
        path += ('&' if '?' in path else '?') + 'project=demo'
        payload = None if data is None else json.dumps(data).encode('utf-8')
        request = Request(self.server.url + path, payload, {'X-Crew': '1'})
        try:
            response = self.opener.open(request, timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            raw = response.read()
            return response.status, json.loads(raw) if response.headers['Content-Type'].startswith('application/json') else raw.decode('utf-8')

    def test_manual_todo_state_resolve_and_undo_do_not_change_decisions(self):
        item = todo.add(self.conn, 's1', '亲手验收编辑器', '打开测试关卡，点击运行', '人物动作和预期一致', blocking=True)
        before = self.request('/api/state')[1]
        self.assertEqual(before['todo_count'], 1)
        self.assertEqual(before['decisions'], [])
        code, done = self.request('/api/todo/resolve', {'id': item['id'], 'status': 'done', 'note': '已实测', 'expected_version': 1})
        self.assertEqual(code, 200, done)
        self.assertEqual(self.request('/api/todos')[1]['todos'], [])
        code, undo = self.request('/api/todo/undo', {'id': item['id'], 'before': done['before'], 'version': done['version']})
        self.assertEqual(code, 200, undo)
        self.assertEqual(self.request('/api/state')[1]['todo_count'], 1)

    def test_codex_wake_and_offline_notices_do_not_require_claude_cli(self):
        service = self.server.service
        runtimes.set(self.conn, 's1', 'codex', native_id='native-one')
        runtimes.set(self.conn, 's3', 'codex', native_id='native-leader')
        service.agents_ready.clear()
        service.agents_error = 'Claude CLI is not installed'
        with mock.patch.object(service.chat, 'wake', return_value={'ok': True}) as wake:
            code, result = self.request('/api/wake', {'session_id': 's1', 'text': '接着干'})
        self.assertEqual(code, 200, result)
        wake.assert_called_once()
        service.chat.path('s3').parent.mkdir(parents=True, exist_ok=True)
        service.chat.path('s3').touch()
        notice_id = board.queue_leader_notice(self.conn, 's3', '测试无 Claude 时的通知', 'crew')
        with mock.patch.object(service.chat, 'send', return_value={'ok': True}) as send:
            service.deliver_notices()
        send.assert_called_once()
        self.assertEqual(board.pending_notices(self.conn, 's3'), [])

    def test_mixed_decision_reply_uses_crew_message_transport(self):
        runtimes.set(self.conn, 's1', 'codex', native_id='native-member')
        number = board.ask(self.conn, 's1', '选择验收时间', options=('今天验收', '明天验收'))
        code, result = self.request('/api/answer', {'id': number, 'text': '明天验收'})
        self.assertEqual(code, 200, result)
        notice = board.pending_notices(self.conn, 's3')[0]['text']
        self.assertIn('crew send one', notice)
        self.assertNotIn('用 SendMessage', notice)

    def test_trust_goal_is_explicit_and_report_is_downloadable(self):
        self.assertEqual(self.request('/api/trust', {'enabled': True, 'goal': ''})[0], 400)
        self.assertEqual(self.request('/api/trust', {'enabled': True, 'goal': '交付可复查的测试目标','leader_session_id':'s3'})[0], 200)
        state = self.request('/api/state')[1]
        self.assertTrue(next(leader for leader in state['leaders'] if leader['session_id']=='s3')['trust']['enabled'])
        self.assertNotIn('trust',state)
        code, report = self.request('/api/trust/report?leader_session_id=s3')
        self.assertEqual(code, 200)
        self.assertIn('交付可复查的测试目标', report)
        self.assertEqual(self.request('/api/trust/report')[0],400)
        self.assertEqual(self.request('/api/trust', {'enabled':True,'goal':'bad member target','leader_session_id':'s1'})[0],400)

    def test_runtime_prepare_confirm_and_idempotent_retry_are_scoped_and_never_start_a_process(self):
        catalog=[{'id':'claude','available':True},{'id':'codex','available':True,'models':[]}]
        self.server.service.runtime_catalog=mock.Mock(snapshot=lambda:catalog,refresh=lambda **kw:catalog)
        code,preview=self.request('/api/session/runtime/prepare',{'session_id':'s1','runtime':'codex'})
        self.assertEqual(code,200,preview);self.assertFalse(preview['starts_turn'])
        self.assertEqual(runtimes.get(self.conn,'s1')['runtime'],'claude')
        self.assertEqual(self.request('/api/session/runtime/switch',{'session_id':'s1','preview_id':preview['preview_id'],'confirm':False})[0],400)
        code,result=self.request('/api/session/runtime/switch',{'session_id':'s1','preview_id':preview['preview_id'],'confirm':True})
        self.assertEqual(code,200,result);self.assertEqual(result['status'],'configured')
        self.assertFalse(result['starts_turn']);self.assertEqual(self.processes,[])
        self.server.service.runtime_catalog.refresh=mock.Mock(side_effect=AssertionError('receipt retries must not inspect native tools'))
        self.assertEqual(self.request('/api/session/runtime/switch',{'session_id':'s1','preview_id':preview['preview_id'],'confirm':True})[1],result)
        board.init_project(self.conn,'foreign',self.root/'foreign')
        code,_=self.request('/api/session/runtime/prepare?project=foreign',{'session_id':'s1','runtime':'claude'})
        self.assertEqual(code,400)

    def test_runtime_stale_confirmation_is_409_and_state_disables_external_sessions(self):
        catalog=[{'id':'claude','available':True},{'id':'codex','available':True,'models':[]}]
        self.server.service.runtime_catalog=mock.Mock(snapshot=lambda:catalog,refresh=lambda **kw:catalog)
        code,preview=self.request('/api/session/runtime/prepare',{'session_id':'s1','runtime':'codex'})
        self.assertEqual(code,200,preview)
        self.server.service.chat.journal('s1').message('new-message','changed after preview')
        self.assertEqual(self.request('/api/session/runtime/switch',{'session_id':'s1','preview_id':preview['preview_id'],'confirm':True})[0],409)
        with self.server.service.agents_lock:
            self.server.service.agents=[{'sessionId':'s1','pid':32,'name':'external','status':'idle','cwd':str(self.project)}]
        state=self.request('/api/state')[1]
        member=next(row for row in state['members'] if row['session_id']=='s1')
        self.assertFalse(member['runtime_switch']['allowed']);self.assertIn('外部',member['runtime_switch']['reason'])

    def test_old_question_keeps_its_original_leader_after_member_moves(self):
        number=board.ask(self.conn,'s1','Original group owns this question',options=('yes','no'))
        ungrouped=board.ask(self.conn,'s2','No leader owned this question',options=('yes','no'))
        board.join(self.conn,'new-chief','demo','other',self.project,self.project,'main',role='leader')
        board.roster(self.conn,'demo','move','other',session_ids=['s1','s2'])
        state=self.request('/api/state')[1]
        scopes={row['id']:row['leader_session_id'] for row in state['decisions']}
        self.assertEqual(scopes[number],'s3');self.assertIsNone(scopes[ungrouped])
        code,result=self.request('/api/answer',{'id':number,'text':'yes'})
        self.assertEqual(code,200,result);self.assertEqual(result['routed_to'],'lead')
        self.assertEqual(board.pending_notices(self.conn,'new-chief'),[])
        self.assertIn('Original group owns this question',board.pending_notices(self.conn,'s3')[0]['text'])
        code,result=self.request('/api/answer',{'id':ungrouped,'text':'yes'})
        self.assertEqual(code,200,result);self.assertIsNone(result['routed_to'])
        self.assertEqual(board.pending_notices(self.conn,'new-chief'),[])

    def test_usage_scan_does_not_upgrade_a_stale_snapshot_after_a_service_writer(self):
        service=self.server.service
        service.scanned_at=None
        snapshot=threading.Event();attempted=threading.Event();written=threading.Event()
        writer_errors=[]
        def scan(conn,*args):
            # Reproduce SQLite's deferred read -> write hazard. The competing
            # service writer must wait until this complete scan releases its lock.
            conn.execute('SAVEPOINT timing_scan')
            conn.execute('SELECT COUNT(*) FROM sessions').fetchone()
            snapshot.set()
            if not attempted.wait(3):raise AssertionError('writer never attempted')
            written.wait(.15)
            conn.execute('PRAGMA user_version=2')
            conn.execute('RELEASE timing_scan')
        def write():
            try:
                if not snapshot.wait(3):raise AssertionError('scan never read')
                attempted.set()
                with service.write_lock,closing(store.connect(self.home)) as conn:
                    conn.execute("UPDATE sessions SET last_prompt='parallel service writer' WHERE session_id='s1'")
                written.set()
            except BaseException as exc:writer_errors.append(exc)
        writer=threading.Thread(target=write)
        writer.start()
        try:
            with mock.patch('crew.web.usage.scan',side_effect=scan):service.scan_usage()
        finally:writer.join(5)
        self.assertFalse(writer.is_alive());self.assertEqual(writer_errors,[])
        self.assertTrue(written.is_set())

    def test_comments_have_project_scope_and_notify_leader(self):
        code, comment = self.request('/api/comments', {'session_id': 's1', 'text': '请解释这里的进度'})
        self.assertEqual(code, 200, comment)
        self.assertEqual(self.request('/api/comments?session_id=s1')[1]['comments'][0]['text'], '请解释这里的进度')
        self.assertIn('用户在总览中', board.pending_notices(self.conn, 's3')[0]['text'])
        board.init_project(self.conn, 'elsewhere', self.root / 'elsewhere')
        board.join(self.conn, 'foreign', 'elsewhere', 'foreign', self.root, None, None)
        self.assertEqual(self.request('/api/comments', {'session_id': 'foreign', 'text': 'wrong'})[0], 400)

    def test_existing_question_can_be_sent_for_clarification_once(self):
        number = board.ask(self.conn, 's1', '很长但无法理解的旧问题', options=('继续①②', '先等着'))
        code, first = self.request('/api/decision/clarify', {'id': number})
        self.assertEqual(code, 200, first)
        code, repeated = self.request('/api/decision/clarify', {'id': number})
        self.assertEqual(code, 200, repeated)
        self.assertIsNotNone(first['notice_id'])
        self.assertIsNone(repeated['notice_id'])
        self.assertEqual(len(board.pending_notices(self.conn, 's3')), 1)
        self.assertIsNone(self.request('/api/state')[1]['decisions'][0]['brief'])
