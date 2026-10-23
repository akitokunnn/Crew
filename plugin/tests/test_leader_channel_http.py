"""Leader wake permissions and channel HTTP with isolated storage and fake processes."""
import tests  # Install the production-service connection guard.
import json
from contextlib import closing
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from crew import board, channel, session_ops, store, web
from test_shell import NOW, PLUGIN, ShellFixture
import test_channel_digest as digest_fixture


class LeaderWakeTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        board.update_cc_name(self.conn, 's3', 'cc-lead', now=NOW)
        board.join(self.conn, 'other', 'demo', 'git', self.project, self.project, 'main',
                   role='leader', cc_name='cc-git', now=NOW)

    def wake(self, target='s1', caller='s3'):
        with mock.patch('crew.chat.shutil.which', return_value='claude.exe'):
            return self.manager.wake(self.conn, board.session(self.conn, target), '核对分支',
                                     caller_session_id=caller, now=NOW)

    def message(self, target='s1'):
        return next(event for event in self.manager.journal(target).events if event['type'] == 'message')

    def test_own_leader_introduction_is_unchanged(self):
        self.wake()
        self.assertEqual(self.message()['text'],
                         '[Crew] 你的 leader「lead」把你叫醒了（你原来的会话进程已经结束，Crew 在后台接着运行你）：\n\n'
                         '核对分支\n\n回复 leader 用 SendMessage，地址「cc-lead」。')

    def test_cross_group_leader_names_authority_and_reply_address(self):
        self.wake(caller='other')
        message = self.message()
        self.assertIn('「git」组的 leader「git」找你。它不是你的 leader：它的话是请求和信息', message['text'])
        self.assertIn('先问你自己的 leader「lead」', message['text'])
        self.assertIn('回复 leader 用 SendMessage，地址「cc-git」', message['text'])
        self.assertEqual(message['sender'], 'git')

    def test_ungrouped_adopted_member_can_be_woken_without_own_leader_clause(self):
        self.wake('s2', 'other')
        self.assertIn('它不是你的 leader', self.message('s2')['text'])
        self.assertNotIn('先问你自己的 leader', self.message('s2')['text'])

    def test_leader_target_queues_notice_without_process_or_liveness_check(self):
        with mock.patch.object(self.manager, '_check_offline') as check:
            result = self.wake('s3', 'other')
        check.assert_not_called()
        self.assertEqual(self.processes, [])
        self.assertFalse(self.manager.path('s3').exists())
        self.assertEqual(result['message'], '已排进对方的通知，对方空闲时送达')
        notice = board.pending_notices(self.conn, 's3')[0]
        self.assertEqual(notice['source'], 'crew')
        self.assertEqual(notice['text'], 'leader「git」（git）找你：核对分支。回复用 SendMessage，地址「cc-git」，或 crew say --to git。')

    def test_member_cannot_wake(self):
        with self.assertRaisesRegex(board.BoardError, '只有同项目的 leader'):
            self.wake(caller='s2')
        self.assertEqual(self.processes, [])

    def test_human_leader_target_errors_with_web_instruction(self):
        with self.assertRaisesRegex(board.BoardError, 'leader 直接在网页上和它聊'):
            self.wake('s3', board.HUMAN)

    def test_human_can_wake_member(self):
        self.wake(caller=board.HUMAN)
        self.assertIn('[Crew] 用户把你叫醒了', self.message()['text'])
        self.assertIn('地址「cc-lead」', self.message()['text'])

    def test_archived_removed_and_closed_targets_still_rejected(self):
        for target, removed, expected in [('s1', False, '已归档'), ('s2', True, '已移除')]:
            session_ops.archive(self.conn, target, removed=removed, now=NOW)
            with self.subTest(target=target), self.assertRaisesRegex(board.BoardError, expected):
                self.wake(target, 'other')
        self.conn.execute("UPDATE sessions SET status='closed' WHERE session_id='s3'")
        with self.assertRaisesRegex(board.BoardError, '已收'):
            self.wake('s3', 'other')
        self.assertEqual(self.processes, [])

    def test_leader_from_another_project_is_rejected(self):
        board.init_project(self.conn, 'another', self.root / 'another', now=NOW)
        board.join(self.conn, 'foreign', 'another', 'foreign', self.root / 'another', None, None,
                   role='leader', now=NOW)
        with self.assertRaisesRegex(board.BoardError, '同项目'):
            self.wake(caller='foreign')

    def test_closed_group_or_archived_caller_is_rejected(self):
        session_ops.close_group(self.conn, 'demo', 'git', with_members=False, now=NOW)
        with self.assertRaisesRegex(board.BoardError, '同项目的 leader'):
            self.wake(caller='other')
        session_ops.archive(self.conn, 's3', now=NOW)
        with self.assertRaisesRegex(board.BoardError, '同项目的 leader'):
            self.wake('s2')
        self.assertEqual(self.processes, [])

    def test_closed_leader_group_and_unadopted_target_are_rejected(self):
        session_ops.close_group(self.conn, 'demo', 'git', with_members=False, now=NOW)
        with self.assertRaisesRegex(board.BoardError, '目标已归档，不能叫醒'):
            self.wake('other')
        board.register_session(self.conn, 'unnamed', 'demo', self.project, None, None, now=NOW)
        with self.assertRaisesRegex(board.BoardError, '目标未收编'):
            self.wake('unnamed')
        self.assertEqual(self.processes, [])
        self.assertEqual(board.pending_notices(self.conn, 'other'), [])

    def test_cli_leader_wake_formats_notice_result(self):
        result = {'ok': True, 'notice_id': 1, 'message': '已排进对方的通知，对方空闲时送达'}
        with mock.patch('crew.spawn.request_start', return_value=result):
            code, output, errors = self.run_cli(['--session', 'other', 'wake', 'lead', '核对'])
        self.assertEqual(code, 0, errors)
        self.assertEqual(output.strip(), '已排进对方的通知，对方空闲时送达')


class ChannelHttpTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        poll = mock.patch.object(web.Service, '_poll_notices', return_value=None)
        poll.start()
        self.addCleanup(poll.stop)
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=lambda: [], claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=store.parse_iso(NOW), process_factory=self.process_factory,
            notification_client=mock.Mock(), lock_status=lambda: False,
            vscode_state_db=self.vscode_state_db)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.opener = build_opener(ProxyHandler({}))

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

    def request(self, path, data=None, **query):
        if query:
            path += '?' + urlencode(query)
        payload = None if data is None else json.dumps(data).encode('utf-8')
        request = Request(self.server.url + path, payload, {'X-Crew': '1'})
        try:
            response = self.opener.open(request, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def state(self):
        code, result = self.request('/api/state', project='demo')
        self.assertEqual(code, 200, result)
        return result

    def test_user_post_and_mention(self):
        code, result = self.request('/api/channel', {'body': '**用户安排**\n核对 develop', 'to': ['lead']}, project='demo')
        self.assertEqual(code, 200, result)
        message = result['message']
        self.assertEqual(message['speaker_session_id'], board.HUMAN)
        self.assertEqual(message['speaker_name'], '用户')
        self.assertEqual(message['mentions'], [{'session_id': 's3', 'name': 'lead'}])
        notice = board.pending_notices(self.conn, 's3')[0]
        self.assertEqual(notice['source'], 'web')
        self.assertIn('用户点名你', notice['text'])
        self.assertIn('这是用户本人的话', notice['text'])
        self.assertIn(message['body'], notice['text'])
        self.assertEqual(self.state()['channel'], {'unread': 0, 'latest_id': message['id'], 'digesting': False, 'digest_error': None, 'digest_id': None})

    def digest_process(self, process):
        manager = self.server.service.channel_digest
        manager.process_factory = mock.Mock(return_value=process)
        executable = mock.patch('crew.chat.shutil.which', return_value='claude.exe')
        executable.start()
        self.addCleanup(executable.stop)
        self.addCleanup(manager.close)
        return manager

    def finish_digest(self, manager):
        for worker in manager.workers.values():
            worker.join(5)
            self.assertFalse(worker.is_alive())

    def test_digest_http_no_new_concurrent_and_success_fields(self):
        manager = self.digest_process(digest_fixture.FakeProcess())
        code, result = self.request('/api/channel/digest', {'project': 'demo'})
        self.assertEqual(code, 400)
        self.assertIn('没有新发言需要整理', result['error'])
        self.assertIsNone(self.request('/api/channel', project='demo')[1]['digest'])
        post = channel.post(self.conn, 'demo', 's3', '待整理全文', now=NOW)
        gate = threading.Event()
        process = digest_fixture.FakeProcess(gate=gate)
        manager.process_factory.return_value = process
        try:
            self.assertEqual(self.request('/api/channel/digest', {'project': 'demo'}), (200, {'ok': True}))
            self.assertTrue(process.entered.wait(5))
            self.assertTrue(self.state()['channel']['digesting'])
            code, result = self.request('/api/channel/digest', {'project': 'demo'})
            self.assertEqual(code, 400)
            self.assertIn('正在整理', result['error'])
        finally:
            gate.set()
        self.finish_digest(manager)
        card = self.request('/api/channel', project='demo')[1]['digest']
        self.assertEqual((card['body'], card['upto_message_id']), (digest_fixture.CARD, post['id']))
        self.assertEqual(self.state()['channel'], {'unread': 1, 'latest_id': post['id'], 'digesting': False,
                                                  'digest_error': None, 'digest_id': card['id']})

    def test_digest_http_failure_reason_and_manual_retry(self):
        manager = self.digest_process(digest_fixture.FakeProcess(code=8, errors='真实鉴权错误'))
        channel.post(self.conn, 'demo', 's3', '待整理', now=NOW)
        self.assertEqual(self.request('/api/channel/digest', {'project': 'demo'})[0], 200)
        self.finish_digest(manager)
        state = self.state()['channel']
        self.assertFalse(state['digesting'])
        self.assertIn('RuntimeError: 退出码 8；stderr: 真实鉴权错误', state['digest_error'])
        self.assertIsNone(state['digest_id'])
        self.assertIsNone(self.request('/api/channel', project='demo')[1]['digest'])
        manager.process_factory.return_value = digest_fixture.FakeProcess()
        self.assertEqual(self.request('/api/channel/digest', {'project': 'demo'})[0], 200)
        self.finish_digest(manager)
        self.assertIsNone(self.state()['channel']['digest_error'])

    def test_periodic_digest_detects_direct_database_posts(self):
        manager = self.digest_process(digest_fixture.FakeProcess())
        self.assertTrue(self.server.service.agents_ready.wait(5))
        for n in range(19):
            channel.post(self.conn, 'demo', 's3', str(n), now=NOW)
        self.server.service.digest_channels()
        manager.process_factory.assert_not_called()
        channel.post(self.conn, 'demo', 's3', 'twentieth', now=NOW)
        self.server.service.digest_channels()
        self.finish_digest(manager)
        self.assertEqual(channel.digest(self.conn, 'demo')['trigger'], 'auto')

    def test_digest_project_body_query_conflict_is_rejected(self):
        board.init_project(self.conn, 'another', self.root / 'another', now=NOW)
        code, result = self.request('/api/channel/digest', {'project': 'another'}, project='demo')
        self.assertEqual(code, 400)
        self.assertIn('项目不一致', result['error'])

    def test_pagination_and_read_state(self):
        first = channel.post(self.conn, 'demo', 's3', 'first\n全文', now=NOW)
        second = channel.post(self.conn, 'demo', 's3', 'second', now=NOW)
        self.assertEqual(self.state()['channel'], {'unread': 2, 'latest_id': second['id'], 'digesting': False, 'digest_error': None, 'digest_id': None})
        code, result = self.request('/api/channel', project='demo', limit=1)
        self.assertEqual(code, 200, result)
        self.assertEqual([item['id'] for item in result['messages']], [second['id']])
        self.assertTrue(result['has_more'])
        code, earlier = self.request('/api/channel', project='demo', before=second['id'], limit=1)
        self.assertEqual(code, 200, earlier)
        self.assertEqual(earlier['messages'][0]['body'], 'first\n全文')
        self.assertFalse(earlier['has_more'])
        code, read = self.request('/api/channel/read', {'id': second['id']}, project='demo')
        self.assertEqual(code, 200, read)
        self.assertEqual(read, {'read_id': second['id'], 'unread': 0, 'latest_id': second['id']})
        self.assertEqual(self.state()['channel']['unread'], 0)
        self.request('/api/channel/read', {'id': first['id']}, project='demo')
        self.assertEqual(self.state()['channel']['unread'], 0)

    def test_unknown_mention_and_invalid_read_fail_without_write(self):
        code, _ = self.request('/api/channel', {'body': 'hello', 'to': ['missing']}, project='demo')
        self.assertEqual(code, 400)
        self.assertEqual(channel.messages(self.conn, 'demo')['messages'], [])
        code, _ = self.request('/api/channel/read', {'id': -1}, project='demo')
        self.assertEqual(code, 400)

    def test_json_project_routes_and_conflicting_project_is_rejected(self):
        board.init_project(self.conn, 'another', self.root / 'another', now=NOW)
        code, result = self.request('/api/channel', {'project': 'another', 'body': '另一个项目'})
        self.assertEqual(code, 200, result)
        self.assertEqual(result['message']['project'], 'another')
        self.assertEqual(channel.messages(self.conn, 'demo')['messages'], [])
        code, result = self.request('/api/channel/read', {'project': 'another', 'id': result['message']['id']})
        self.assertEqual(code, 200, result)
        code, _ = self.request('/api/channel', {'project': 'another', 'body': '冲突'}, project='demo')
        self.assertEqual(code, 400)

    def test_notice_send_has_no_write_transaction_and_allows_other_writers(self):
        service = self.server.service
        with mock.patch('crew.chat.shutil.which', return_value='claude.exe'):
            result = service.chat.start(self.conn, 'demo', 'receiver', '协调', role='leader', now=NOW)
        sid = result['session_id']
        service.chat.journal(sid).set_status('idle')
        channel.post(self.conn, 'demo', 's3', 'only once', to=['receiver'], now=NOW)
        real_send = service.chat.send

        def send_and_write(session, *args, **kwargs):
            self.assertFalse(pending.call_args.args[0].in_transaction)
            with closing(store.connect(self.home)) as other:
                other.execute('PRAGMA busy_timeout = 0')
                board.record_event(other, 'test:notice_send', 'demo', sid, {}, now=NOW)
                self.assertIsNone(other.execute('SELECT sent_at FROM leader_notices WHERE session_id=?',
                                               (sid,)).fetchone()[0])
                self.assertIsNone(channel.summary(other, sid, now=NOW))
            return real_send(session, *args, **kwargs)

        with mock.patch.object(board, 'pending_notices', wraps=board.pending_notices) as pending:
            with mock.patch.object(service.chat, 'send', side_effect=send_and_write):
                service.deliver_notices()
        self.assertEqual(board.pending_notices(self.conn, sid), [])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM events WHERE kind='test:notice_send'").fetchone()[0], 1)

    def test_failed_notice_send_keeps_mention_pending(self):
        service = self.server.service
        service.chat.journal('s3').message('earlier', 'earlier')
        channel.post(self.conn, 'demo', board.HUMAN, 'ordinary', source='web', now=NOW)
        channel.post(self.conn, 'demo', board.HUMAN, 'must remain', to=['lead'], source='web', now=NOW)
        self.assertTrue(service.agents_ready.wait(3))
        with mock.patch.object(service.chat, 'send', side_effect=OSError('fixture send failed')):
            service.deliver_notices()
        self.assertEqual(len(board.pending_notices(self.conn, 's3')), 1)
        self.assertIsNone(self.conn.execute("SELECT sent_at FROM leader_notices WHERE session_id='s3'").fetchone()[0])
        summary = channel.summary(self.conn, 's3', now=NOW)
        self.assertIn('ordinary', summary)
        self.assertNotIn('must remain', summary)

