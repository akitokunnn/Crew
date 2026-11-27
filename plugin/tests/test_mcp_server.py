"""The registry-generated MCP server: tokens, tool lists per role and runtime, search, calls (design §5)."""
import tests
from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from crew import board, channel, guard, hooks, runtimes, store, war_room, web
from crew.ops import guide, launch, registry, tokens
from test_shell import NOW, PLUGIN, ShellFixture


class LiveProcess:
    def __init__(self):
        self.code = None

    def poll(self):
        return self.code


class McpServerTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
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

    def grant(self, session_id, runtime='claude'):
        if runtime == 'codex':
            runtimes.set(self.conn, session_id, 'codex', now=NOW)
        token = tokens.issue(self.conn, session_id=session_id, runtime=runtime, now=NOW)
        process = LiveProcess()
        launch.attach(launch.Grant(token, None, None, runtime, 'core'), process)
        return token, process

    def rpc(self, token, method, params=None, ident=1, raw=False):
        body = {'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params or {}}
        request = Request(self.url + '/mcp', json.dumps(body).encode('utf-8'),
                          {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
                           'Accept': 'application/json, text/event-stream'})
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            content = response.read().decode('utf-8')
            if raw:
                return response.status, response.headers.get('Content-Type'), content
            return response.status, json.loads(content) if content else None

    def tools(self, token):
        return {tool['name']: tool for tool in self.rpc(token, 'tools/list')[1]['result']['tools']}

    def call(self, token, name, arguments):
        status, reply = self.rpc(token, 'tools/call', {'name': name, 'arguments': arguments})
        self.assertEqual(status, 200)
        result = reply['result']
        return result['isError'], json.loads(result['content'][0]['text'])

    def test_initialize_and_notifications(self):
        token, _ = self.grant('s3')
        status, reply = self.rpc(token, 'initialize', {'protocolVersion': '2025-06-18'})
        self.assertEqual(reply['result']['serverInfo']['name'], 'crew')
        self.assertTrue(reply['result']['capabilities']['tools']['listChanged'])
        request = Request(self.url + '/mcp', json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}).encode(),
                          {'Authorization': 'Bearer ' + token})
        with urlopen(request, timeout=10) as response:
            self.assertEqual((response.status, response.read()), (202, b''))

    def test_core_tools_per_role_and_runtime(self):
        leader, _ = self.grant('s3')
        names = set(self.tools(leader))
        expected = {op.tool_name for op in registry.operations().values() if 'leader' in op.core_for} | {'crew_search'}
        self.assertEqual(names, expected)
        self.assertIn('channel_post', names)
        self.assertNotIn('crew_call', names)
        member, _ = self.grant('s1', runtime='codex')
        names = set(self.tools(member))
        self.assertIn('crew_call', names)
        self.assertNotIn('channel_post', names)
        self.assertNotIn('execution_spawn', names)
        tool = self.tools(leader)['message_send']
        self.assertIn('request_id', tool['inputSchema']['required'])
        self.assertFalse(tool['annotations']['readOnlyHint'])

    def test_git_admin_may_reply_only_to_delivered_mentions_in_its_workspace(self):
        board.enable_git(self.conn, 'demo', base='main', admin_session_id='s3', now=NOW)
        board.join(self.conn, 'author', 'demo', '发帖人', self.project, None, None, role='leader', now=NOW)
        board.join(self.conn, 'third', 'demo', '第三人', self.project, None, None, role='leader', now=NOW)
        board.init_project(self.conn, 'foreign', self.root / 'foreign', now=NOW)
        foreign = channel.post(self.conn, 'foreign', board.HUMAN, '别的工作区', source='web', now=NOW)
        other = channel.post(self.conn, 'demo', board.HUMAN, '点名别人', ['第三人'], source='web', now=NOW)

        for runtime in ('claude', 'codex'):
            with self.subTest(runtime=runtime):
                token, _ = self.grant('s3', runtime)
                names = self.tools(token)
                self.assertIn('channel_post', names)
                self.assertNotIn('channel_consume', names)
                error, me = self.call(token, 'session_whoami', {})
                self.assertFalse(error)
                self.assertEqual((me['role'], me['mcp_role']), ('leader', 'git_admin'))
                original = channel.post(self.conn, 'demo', 'author', '请确认分支', ['lead'], now=NOW)
                notice_id = self.conn.execute('SELECT notice_id FROM channel_mentions WHERE message_id=?',
                                              (original['id'],)).fetchone()[0]
                self.assertIn('reply_to=%d' % original['id'], board.pending_notices(self.conn, 's3')[0]['text'])

                def reply(args, request_id, indirect=False):
                    args = {'text': '已确认', 'request_id': request_id, **args}
                    if indirect:
                        return self.call(token, 'crew_call', {'op': 'channel.post', 'args': args})
                    return self.call(token, 'channel_post', args)

                self.assertTrue(reply({'reply_to': original['id']}, runtime + '-pending')[0])
                board.mark_notice_sent(self.conn, notice_id, now=NOW)
                for indirect in ([False] if runtime == 'claude' else [False, True]):
                    for case, args in enumerate([
                            {}, {'reply_to': foreign['id']}, {'reply_to': other['id']},
                            {'reply_to': original['id'], 'to': ['第三人']},
                            {'reply_to': original['id'], 'to': ['*']},
                            {'reply_to': original['id'], 'text': '也请 @第三人 看'}]):
                        with self.subTest(args=args, indirect=indirect):
                            denied, value = reply(args, f'{runtime}-{indirect}-{case}', indirect)
                            self.assertTrue(denied, value)
                args = {'reply_to': original['id']}
                error, posted = reply(args, runtime + '-ok', runtime == 'codex')
                self.assertFalse(error, posted)
                self.assertEqual((posted['speaker_session_id'], posted['reply_to']), ('s3', original['id']))
                self.assertEqual([target['session_id'] for target in posted['notified']], ['author'])
                self.assertEqual(reply(args, runtime + '-ok', runtime == 'codex'),
                                 (False, {**posted, 'replayed': True}))
        self.assertFalse(any('已确认' in notice['text'] for notice in board.pending_notices(self.conn, 'third')))

    def test_mention_hint_respects_already_issued_scopes(self):
        token, _ = self.grant('s3')
        self.conn.execute('UPDATE mcp_tokens SET own_scopes=? WHERE token_hash=?',
                          (json.dumps(['session.whoami']), tokens.digest(token)))
        channel.post(self.conn, 'demo', board.HUMAN, '点到你', ['lead'], source='web', now=NOW)
        [notice] = board.pending_notices(self.conn, 's3')
        self.assertIn('点到你', notice['text'])
        self.assertNotIn('channel_post', notice['text'])

    def test_first_delivery_refreshes_the_hint_after_the_grant_changes(self):
        token, _ = self.grant('s3')
        channel.post(self.conn, 'demo', board.HUMAN, '请看看原帖', ['lead'], source='web', now=NOW)
        [notice] = board.pending_notices(self.conn, 's3')
        self.assertIn('channel_post', notice['text'])
        self.conn.execute('UPDATE mcp_tokens SET own_scopes=? WHERE token_hash=?',
                          (json.dumps(['session.whoami']), tokens.digest(token)))
        with mock.patch.object(self.service.chat, 'send', return_value={'accepted': True}) as send:
            self.service.chat.deliver_leader_notices(self.conn, frozenset(), NOW)
        sent_text = send.call_args.args[1]
        self.assertIn('请看看原帖', sent_text)
        self.assertNotIn('channel_post', sent_text)
        self.assertEqual(board.pending_notices(self.conn, 's3'), [])

    def test_git_admin_room_reply_checks_membership_and_open_status(self):
        board.enable_git(self.conn, 'demo', base='main', admin_session_id='s3', now=NOW)
        board.join(self.conn, 'author', 'demo', '牵头人', self.project, None, None, role='leader', now=NOW)
        board.join(self.conn, 'third', 'demo', '成员', self.project, None, None, role='leader', now=NOW)
        token, _ = self.grant('s3')
        room = war_room.start(self.conn, 'demo', 'author', '一起核对', '确认分支', ['lead', '成员'], now=NOW)
        original = room['first_message']
        notice_id = self.conn.execute('SELECT notice_id FROM channel_mentions WHERE message_id=? AND session_id=?',
                                      (original['id'], 's3')).fetchone()[0]
        [notice] = board.pending_notices(self.conn, 's3')
        self.assertIn('channel_post', notice['text'])
        self.assertNotIn('war_room_', notice['text'])
        board.mark_notice_sent(self.conn, notice_id, now=NOW)

        def reply(identity):
            return self.call(token, 'channel_post', {'text': '已经核对', 'reply_to': original['id'],
                                                      'request_id': identity})

        denied, posted = reply('room-open')
        self.assertFalse(denied, posted)
        self.assertEqual(posted['room_id'], room['id'])
        war_room.change_members(self.conn, room['id'], 'author', remove=['lead'], now=NOW)
        self.assertTrue(reply('room-left')[0])
        war_room.change_members(self.conn, room['id'], 'author', add=['lead'], now=NOW)
        war_room.close(self.conn, room['id'], 'author', '完成', now=NOW)
        self.assertTrue(reply('room-closed')[0])

    def test_search_loads_tools_for_claude_with_list_changed(self):
        token, _ = self.grant('s3')
        self.assertNotIn('handoff_add', self.tools(token))
        status, kind, body = self.rpc(token, 'tools/call', {'name': 'crew_search', 'arguments': {'query': '交接'}}, raw=True)
        self.assertEqual(status, 200)
        self.assertTrue(kind.startswith('text/event-stream'))
        messages = [json.loads(line[len('data: '):]) for line in body.splitlines() if line.startswith('data: ')]
        self.assertEqual(messages[0]['method'], 'notifications/tools/list_changed')
        hits = json.loads(messages[1]['result']['content'][0]['text'])['results']
        self.assertIn('handoff.add', [hit['op'] for hit in hits])
        self.assertIn('handoff_add', self.tools(token))

    def test_a_resumed_claude_conversation_keeps_the_tools_it_found(self):
        # Real Haiku run (2026-11-27): after a restart the resumed assistant called goal_create from its own
        # history, Claude Code answered "No such tool available", and the assistant gave up.
        token, _ = self.grant('s3')
        self.rpc(token, 'tools/call', {'name': 'crew_search', 'arguments': {'query': '交接'}}, raw=True)
        self.assertIn('handoff_add', self.tools(token))
        tokens.revoke_all(self.conn, NOW)
        resumed, _ = self.grant('s3')
        self.assertIn('handoff_add', self.tools(resumed))
        other, _ = self.grant('s1')
        self.assertNotIn('handoff_add', self.tools(other))

    def test_codex_calls_searched_operations_through_crew_call(self):
        token, _ = self.grant('s1', runtime='codex')
        error, found = self.call(token, 'crew_search', {'query': '进度汇报'})
        self.assertFalse(error)
        hit = next(hit for hit in found['results'] if hit['op'] == 'report.add')
        self.assertIn('crew_call', hit['how'])
        self.assertNotIn('report_add', self.tools(token))
        error, value = self.call(token, 'crew_call', {'op': 'report.add', 'args': {'text': '完成第一步', 'request_id': 'r-1'}})
        self.assertFalse(error, value)
        self.assertEqual(value['body'], '完成第一步')
        error, value = self.call(token, 'crew_call', {'op': 'execution.spawn', 'args': {'name': 'x', 'task': 'y'}})
        self.assertTrue(error)
        self.assertEqual(value['error']['code'], 'forbidden')

    def test_identity_comes_from_the_token(self):
        token, _ = self.grant('s3')
        error, me = self.call(token, 'session_whoami', {})
        self.assertFalse(error)
        self.assertEqual((me['session_id'], me['crew_name']), ('s3', 'lead'))
        error, posted = self.call(token, 'channel_post', {'text': '我来接这个', 'request_id': 'c-1'})
        self.assertFalse(error, posted)
        row = self.conn.execute("SELECT speaker_session_id FROM channel_messages WHERE body='我来接这个'").fetchone()
        self.assertEqual(row[0], 's3')
        error, value = self.call(token, 'message_send', {'to': 'one', 'text': 'x', 'request_id': 'm-1', 'session_id': 's1'})
        self.assertTrue(error)
        self.assertEqual(value['error']['code'], 'invalid_arguments')

    def test_board_hides_closed_groups_and_ended_sessions(self):
        for sid, name, status, seen in (('old', 'old', 'offline', '2026-10-19T08:00:00.000Z'),
                                        ('recent', 'recent', 'offline', '2026-10-21T07:00:00.000Z'),
                                        ('gone', 'gone', 'closed', NOW)):
            board.join(self.conn, sid, 'demo', name, self.project, self.project, 'main', now=NOW)
            self.conn.execute('UPDATE sessions SET status=?, last_event_at=? WHERE session_id=?', (status, seen, sid))
        board.join(self.conn, 'old-lead', 'demo', 'old-lead', self.project, self.project, 'main', role='leader', now=NOW)
        self.conn.execute("UPDATE groups SET closed_at=? WHERE name='old-lead'", (NOW,))
        self.conn.commit()
        token, _ = self.grant('s3')
        error, state = self.call(token, 'workspace_state', {})
        self.assertFalse(error, state)
        self.assertEqual([g['name'] for g in state['groups']], ['lead'])
        self.assertEqual(sorted(s['session_id'] for s in state['ungrouped']), ['recent', 's2'])
        self.assertEqual(state['hidden_ended_sessions'], 2)

    def test_writes_need_request_id_and_run_once(self):
        token, _ = self.grant('s3')
        error, value = self.call(token, 'todo_add', {'title': 't', 'steps': 's', 'verification': 'v'})
        self.assertTrue(error)
        args = {'title': '换电池', 'steps': '打开后盖', 'verification': '灯亮', 'request_id': 't-1'}
        first = self.call(token, 'todo_add', args)[1]
        again = self.call(token, 'todo_add', args)[1]
        self.assertTrue(again['replayed'])
        self.assertEqual(again['id'], first['id'])

    def test_token_dies_with_its_process_and_on_revocation(self):
        starting = tokens.issue(self.conn, session_id='s2', runtime='claude', now=NOW)
        self.assertEqual(self.rpc(starting, 'tools/list')[0], 401)
        launch._processes[tokens.digest(starting)] = None
        self.assertEqual(self.rpc(starting, 'tools/list')[0], 200)
        token, process = self.grant('s3')
        self.assertEqual(self.rpc(token, 'tools/list')[0], 200)
        process.code = 0
        self.assertEqual(self.rpc(token, 'tools/list')[0], 401)
        token, _ = self.grant('s3')
        tokens.revoke_session(self.conn, 's3', NOW)
        self.assertEqual(self.rpc(token, 'tools/list')[0], 401)
        self.assertEqual(self.rpc('not-a-token', 'tools/list')[0], 401)
        request = Request(self.url + '/mcp', b'{}', {'Content-Type': 'application/json'})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=10)
        self.assertEqual(caught.exception.code, 401)

    def test_child_tokens_cannot_exceed_issuer(self):
        parent = tokens.issue(self.conn, session_id='s3', runtime='claude', now=NOW)
        parent_hash = tokens.digest(parent)
        self.conn.execute("UPDATE mcp_tokens SET delegable_scopes=? WHERE token_hash=?",
                          (json.dumps(['session.whoami']), parent_hash))
        self.conn.commit()
        child = tokens.issue(self.conn, session_id='s1', runtime='claude', now=NOW, issuer=parent_hash)
        caller, grants = tokens.caller(self.conn, child)
        self.assertEqual(grants, {'session.whoami'})
        tokens.revoke(self.conn, parent_hash, NOW)
        with self.assertRaisesRegex(Exception, '已吊销'):
            tokens.caller(self.conn, child)

    def test_managed_session_cannot_shell_into_crew(self):
        from crew import install
        install.ensure(self.home, PLUGIN)
        token, _ = self.grant('s3')
        self.assertTrue(hooks.managed(self.home, 's3'))
        self.assertFalse(hooks.managed(self.home, 's2'))
        data = {'session_id': 's3', 'cwd': str(self.project), 'tool_name': 'Bash',
                'tool_input': {'command': 'crew channel post --args-json {}'}}
        self.assertIn('crew MCP', guard.check(self.home, data, now=NOW))
        self.assertIsNone(guard.check(self.home, {**data, 'session_id': 's2'}, now=NOW))

    def test_high_risk_calls_wait_for_the_user(self):
        from unittest import mock
        token, _ = self.grant('s3')
        args = {'goal_id': 'g-missing', 'version_id': 'v1', 'request_id': 'restore-1'}
        error, value = self.call(token, 'goal_version_restore', args)
        self.assertFalse(error, value)
        self.assertEqual(value['status'], 'awaiting_user')
        again = self.call(token, 'goal_version_restore', args)[1]
        self.assertEqual(again['confirmation_id'], value['confirmation_id'])
        listed = self.service.op('interaction.list')['requests']
        self.assertEqual([item['op'] for item in listed], ['goal.version.restore'])
        with mock.patch.object(self.service.chat, 'send') as send:
            failed = self.service.op('interaction.resolve', {'id': value['confirmation_id'], 'approve': True})
        self.assertEqual(failed['status'], 'failed')
        self.assertTrue(failed['error'])
        self.assertIn('执行失败', send.call_args.args[1])
        self.assertEqual(self.service.op('interaction.list')['requests'], [])
        error, other = self.call(token, 'goal_version_restore', {**args, 'request_id': 'restore-2'})
        with mock.patch.object(self.service.chat, 'send') as send:
            rejected = self.service.op('interaction.resolve', {'id': other['confirmation_id'], 'approve': False})
        self.assertEqual(rejected['status'], 'rejected')
        self.assertIn('拒绝', send.call_args.args[1])

    def test_assistant_acts_as_the_user_across_workspaces(self):
        from crew import personal
        assistant = personal.ensure_assistant(self.conn, self.home)['session_id']
        token, _ = self.grant(assistant, runtime='codex')
        tools = self.tools(token)
        self.assertIn('workspace_state', tools)
        self.assertNotIn('channel_consume', tools)
        error, found = self.call(token, 'crew_search', {'query': '交流区发言'})
        self.assertFalse(error)
        error, listed = self.call(token, 'channel_list', {'project': 'demo'})
        self.assertFalse(error, listed)
        error, refused = self.call(token, 'channel_post', {'project': 'demo', 'body': '代用户发', 'request_id': 'a-1'})
        self.assertTrue(error)
        self.assertEqual(refused['error']['code'], 'invalid_arguments')
        self.assertIn('message_id', refused['error']['message'])
        error, refused = self.call(token, 'storage_get', {})
        self.assertTrue(error)

    def test_all_exposure_lists_every_permitted_tool(self):
        token, _ = self.grant('s3', runtime='codex')
        self.service.mcp_exposure = 'all'
        names = set(self.tools(token))
        expected = {op.tool_name for op in registry.operations().values() if 'leader' in op.scopes} | {'crew_search'}
        self.assertEqual(names, expected)
        self.assertNotIn('crew_call', names)
        status, reply = self.rpc(token, 'initialize', {})
        self.assertEqual(reply['result']['capabilities']['tools'], {})

    def test_guides_list_every_operation_of_the_role(self):
        for role in ('leader', 'execution', 'git_admin', 'assistant'):
            text = guide.render(role)
            for op in registry.operations().values():
                if role in op.scopes:
                    self.assertIn('`%s`' % op.tool_name, text)


    def test_only_the_assistant_is_told_to_cite_the_user_message(self):
        self.assertNotIn('message_id', guide.render('leader'))
        self.assertIn('message_id', guide.render('assistant'))
        token, _ = self.grant('s3', runtime='codex')  # Codex gets search results as a plain tool result
        error, found = self.call(token, 'crew_search', {'query': '作战室状态板'})
        self.assertFalse(error, found)
        item = next(result for result in found['results'] if result['op'] == 'war_room.item')
        self.assertIsNone(item['risk'])
        # War room trial feedback: a new item carries no id, and the title limit is in the parameter itself.
        self.assertIn('新增时不要带', item['params']['properties']['id']['description'])
        self.assertEqual(item['params']['properties']['title']['maxLength'], war_room.ITEM_LIMIT)
        self.assertIn('最多 %d 字' % war_room.ITEM_LIMIT, item['params']['properties']['title']['description'])
        self.assertNotIn('project', item['params']['properties'])

if __name__ == '__main__':
    unittest.main()
