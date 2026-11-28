"""Global assistant routes use the real server and isolated fake carriers."""
import tests
import json
import threading
import unittest
from unittest import mock
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from crew import board, store, web
import test_chat_http
from test_shell import ShellFixture, PLUGIN, NOW


class AssistantHttpTests(ShellFixture, unittest.TestCase):
    request = test_chat_http.ChatHttpTests.request
    close_server = test_chat_http.ChatHttpTests.close_server
    finish_turn = test_chat_http.ChatHttpTests.finish_turn

    def setUp(self):
        super().setUp()
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=lambda: [], claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.url = 'http://127.0.0.1:%d' % self.server.server_port

    def open_assistant(self):
        code, value, _ = self.request('/api/op/assistant.open', {})
        self.assertEqual(code, 200, value)
        return value['session_id']

    # The history adapter currently drops the URL query before the handler parses the session ID.
    def test_open_and_state_share_one_global_session_and_never_launch_ai(self):
        before = self.conn.execute('SELECT COUNT(*) FROM projects').fetchone()[0]
        sid = self.open_assistant()
        code, value, _ = self.request('/api/op/assistant.open', {'context_project': 'demo'})
        self.assertEqual(code, 200, value)
        self.assertEqual(value['session_id'], sid)
        code, snapshot, _ = self.request('/api/op/assistant.state')
        self.assertEqual(code, 200, snapshot)
        self.assertIsNone(snapshot['project'])
        self.assertEqual(snapshot['assistant']['session_id'], sid)
        self.assertEqual(snapshot['assistant']['name'], '助手')
        self.assertEqual(snapshot['leaders'], [])
        self.assertEqual(self.processes, [])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM projects').fetchone()[0], before)
        self.assertIsNone(board.session(self.conn, sid)['project'])
        code, value, _ = self.request('/api/op/chat.history?' + urlencode({'session_id': sid}))
        self.assertEqual(code, 200, value)
        self.assertFalse(value['busy'])
        code, _, _ = self.request('/api/op/chat.history?' + urlencode({'project': 'demo', 'session_id': sid}))
        self.assertEqual(code, 400)

    def test_explicit_message_uses_shared_chat_delivery_and_keeps_identity(self):
        sid = self.open_assistant()
        body = {'session_id': sid, 'text': '帮我看看各个项目的近况', 'message_id': 'assistant-http-message'}
        code, value, _ = self.request('/api/op/chat.send', body)
        self.assertEqual(code, 200, value)
        self.assertTrue(value['ok'])
        self.assertEqual(len(self.processes), 1)
        code, again, _ = self.request('/api/op/chat.send', body)
        self.assertEqual(code, 200, again)
        self.assertEqual(again['message_id'], value['message_id'])
        history = self.server.service.chat.history(sid)
        self.assertEqual(len([event for event in history['events'] if event.get('id') == value['message_id']]), 1)
        self.finish_turn(sid, self.processes[0])
        code, result, _ = self.request('/api/op/chat.read', {'session_id': sid, 'seq': 1})
        self.assertEqual(code, 200, result)
        code, result, _ = self.request('/api/op/session.stop', {'session_id': sid})
        self.assertEqual(code, 200, result)
        self.assertEqual(self.open_assistant(), sid)

    def test_first_discussion_needs_only_its_initial_message(self):
        names = []
        for text in ('我想做一个健身打卡 App', '研究流式音频说话人分段，整段结束后400ms内返回'):
            code, value, _ = self.request('/api/op/leader.create?project=demo', {'task': text})
            self.assertEqual(code, 200, value)
            person = board.session(self.conn, value['session_id'])
            names.append(person['crew_name'])
            self.assertTrue(any(event.get('text') == text for event in self.server.service.chat.history(value['session_id'])['events']))
        self.assertEqual(names, ['新对话', '新对话 2'])

    def test_workspace_selector_and_scoped_confirmations_over_http(self):
        from crew import workspace_settings
        from ops_helpers import ai_call
        from test_campaign_launch import plan
        code, opened, _ = self.request('/api/op/assistant.open', {'project': 'demo'})
        self.assertEqual(code, 200, opened)
        sid = opened['session_id']
        code, state, _ = self.request('/api/op/assistant.state?project=demo')
        self.assertEqual(code, 200, state)
        self.assertEqual((state['scope'], state['assistant']['kind'], state['assistant']['project']),
                         ('workspace', 'workspace_assistant', 'demo'))
        self.assertLessEqual({'name', 'display_name', 'theme', 'root'}, set(state['project']))
        self.assertEqual(state['project']['name'], 'demo')
        self.assertEqual(self.processes, [])
        for path, args in (('assistant.state?project=', None), ('assistant.state?project=demo&project=demo', None),
                           ('assistant.state?project=demo&context_project=demo', None),
                           ('assistant.open', {'project': ''}), ('assistant.open', {'project': 'demo', 'context_project': None})):
            self.assertEqual(self.request('/api/op/' + path, args)[0], 400)
        request = ai_call(self.server.service, self.conn, sid, 'campaign.launch', plan(board_version=0))
        rid = request['confirmation_id']
        for suffix, ids in (('', [rid]), ('?project=demo', [rid]), ('?project=other', [])):
            code, value, _ = self.request('/api/op/interaction.list' + suffix)
            self.assertEqual(code, 200, value)
            self.assertEqual([x['id'] for x in value['requests']], ids)
            for card in value['requests']:
                self.assertEqual(card['arg_labels']['project'], '本工作区名，可省略')
        code, refused, _ = self.request('/api/op/interaction.resolve', {'project': 'other', 'id': rid, 'approve': True})
        self.assertEqual((code, refused['error_code']), (404, 'not_visible'))
        workspace_settings.remove(self.conn, 'demo', 0)
        for project in ('demo', 'missing'):
            for op, data in (('assistant.open', {'project': project}), ('assistant.state?project=' + project, None)):
                code, error, _ = self.request('/api/op/' + op, data)
                self.assertEqual((code, error['error_code']), (404, 'project_unavailable'))

    def test_workspace_chat_and_discussion_by_sid(self):
        board.init_project(self.conn, 'other', self.root / 'other')
        sid = self.request('/api/op/assistant.open', {'project': 'demo'})[1]['session_id']
        notes = {}
        for project in ('demo', 'other'):
            notes[project] = self.request('/api/op/note.capture', {'project': project, 'data': {'text': '想法'}})[1]['note']['id']
        send = {'session_id': sid, 'text': '讨论', 'note_id': notes['other']}
        code, error, _ = self.request('/api/op/chat.send?project=demo', send)
        self.assertEqual((code, error['error_code']), (404, 'not_visible'))
        self.assertEqual(self.processes, [])
        send.update(note_id=notes['demo'], message_id='wa-image', images=[{'media_type': 'image/png', 'data': 'eA=='}])
        code, sent, _ = self.request('/api/op/chat.send?project=demo', send)
        self.assertEqual(code, 200, sent)
        self.assertTrue(sent['accepted'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM personal_note_discussions WHERE session_id=?', (sid,)).fetchone()[0], 1)
        query = '?project=demo&session_id=' + sid
        self.assertEqual(self.request('/api/op/chat.history' + query)[0], 200)
        self.assertEqual(self.request('/api/op/chat.read?project=demo', {'session_id': sid, 'seq': 1})[0], 200)
        self.assertEqual(self.request('/api/op/chat.file' + query + '&name=wa-image-1.png', raw=True)[:2], (200, b'x'))
        with mock.patch('crew.native_skills.list_skills', return_value={'skills': []}) as scan:
            self.assertEqual(self.request('/api/op/chat.skills' + query)[:2], (200, {'skills': []}))
            self.assertEqual(store.norm_path(scan.call_args.args[1]), store.norm_path(self.project))
        request = Request(self.url + '/api/chat/events' + query, headers={'X-Crew': '1'})
        with urlopen(request, timeout=5) as stream:
            self.assertEqual(stream.headers.get_content_type(), 'text/event-stream')
            self.assertGreater(int(stream.headers['X-Crew-Latest-Seq']), 0)
            self.assertTrue(stream.readline().startswith(b'id: '))
        self.assertEqual(self.request('/api/op/chat.interrupt?project=demo', {'session_id': sid})[0], 200)

    def test_workspace_runtime_switch_and_public_error_codes(self):
        from crew.ops.errors import OpError
        sid = self.request('/api/op/assistant.open', {'project': 'demo'})[1]['session_id']
        catalog = [{'id': 'claude', 'available': True}, {'id': 'codex', 'available': True, 'models': []}]
        with mock.patch.object(self.server.service.runtime_catalog, 'refresh', return_value=catalog):
            code, preview, _ = self.request('/api/op/session.runtime.prepare?project=demo', {'session_id': sid, 'runtime': 'codex'})
            self.assertEqual(code, 200, preview)
            code, switched, _ = self.request('/api/op/session.runtime.switch?project=demo', {
                'session_id': sid, 'preview_id': preview['preview_id'], 'confirm': True})
            self.assertEqual((code, switched['status']), (200, 'configured'))
        self.assertEqual(self.processes, [])
        for code, public, status in (('project_unavailable', 'project_unavailable', 404), ('not_visible', 'not_visible', 404),
                                     ('version_conflict', 'version_conflict', 409), ('request_conflict', 'request_conflict', 409),
                                     ('outcome_unknown', 'outcome_unknown', 202), ('request_id_reused', 'request_conflict', 409),
                                     ('unknown_outcome', 'outcome_unknown', 409)):
            error = OpError(code, '原因', {'field': 'x'})
            with mock.patch.object(self.server.service, 'post', side_effect=error):
                actual, value, _ = self.request('/api/op/assistant.open', {})
            self.assertEqual((actual, value['code'], value['error_code']), (status, code, public))
            self.assertEqual(error.payload()['error_code'], public)
        self.assertNotIn('error_code', OpError('forbidden', '拒绝').payload())

    def test_new_page_assets_are_served_by_the_actual_backend(self):
        for path in ('/app-data.js', '/assistant.html', '/assistant.js', '/assistant.css', '/workspace-home.js', '/workspace-home.css',
                     '/workspace-entry.js', '/workspace-entry.css',
                     '/global-rail.js', '/persona-ui.js', '/persona-ui.css', '/mindmap.js', '/mindmap-model.js', '/mindmap.css', '/personal-notes-ui.js', '/personal-notes-ui.css', '/behavior-prompts-ui.js', '/behavior-prompts-ui.css', '/channel.css', '/war-room.js', '/war-room.css',
                     '/restructure-ui.js', '/restructure-ui.css'):
            code, content, _ = self.request(path, raw=True)
            self.assertEqual(code, 200, path)
            self.assertGreater(len(content), 30)

    def test_quick_note_capture_edit_and_start_keep_original_and_never_duplicate_leader(self):
        body = {'project': 'demo', 'data': {'text': '简单的健身打卡 App', 'tags': ['生活'], 'project': 'demo'}}
        code, captured, _ = self.request('/api/op/note.capture', body, request_id='fitness-idea')
        self.assertEqual(code, 200, captured)
        note = captured['note']
        code, again, _ = self.request('/api/op/note.capture', body, request_id='fitness-idea')
        self.assertEqual(code, 200, again)
        self.assertEqual(again['note']['id'], note['id'])
        self.assertTrue(again['replayed'])
        self.assertEqual(self.processes, [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM sessions WHERE project IS NULL").fetchone()[0], 0)
        code, updated, _ = self.request('/api/op/note.update', {'project': 'demo', 'data': {'note_id': note['id'], 'expected_version': note['version'], 'text': '健身打卡 App，先做每日打卡',
                     'tags': ['生活'], 'project': 'demo', 'state': 'open'}}, request_id='fitness-edited')
        self.assertEqual(code, 200, updated)
        self.assertEqual(updated['note']['original_text'], body['data']['text'])
        code, listing, _ = self.request('/api/op/notes.list?project=demo')
        self.assertEqual(code, 200, listing)
        self.assertEqual(len(listing['notes']), 1)
        start = {'note_id': note['id'], 'project': 'demo'}
        code, started, _ = self.request('/api/op/note.start', start, request_id='fitness-start')
        self.assertEqual(code, 200, started)
        code, repeated, _ = self.request('/api/op/note.start', start, request_id='fitness-start')
        self.assertEqual(code, 200, repeated)
        self.assertEqual(started['session_id'], repeated['session_id'])
        self.assertEqual(len(self.processes), 1)
        # A lost response can be explicitly associated with the already-created
        # discussion, without replaying the idea into the running conversation.
        self.conn.execute("UPDATE personal_note_leaders SET status='unknown',error='response lost' WHERE note_id=?", (note['id'],))
        code, recovery, _ = self.request('/api/op/note.recovery.get?project=demo&note_id=' + note['id'])
        self.assertEqual(code, 200, recovery)
        self.assertIn(started['session_id'], [person['session_id'] for person in recovery['leaders']])
        code, recovered, _ = self.request('/api/op/note.recover', {'project': 'demo', 'note_id': note['id'],
            'session_id': started['session_id'], 'reason': '已经核对原讨论'}, request_id='fitness-recovered')
        self.assertEqual(code, 200, recovered)
        self.assertEqual(recovered['model_calls'], 0)
        self.assertEqual(len(self.processes), 1)

    def test_wall_project_contract_and_legacy_pool_over_http(self):
        for path in ('notes.list', 'tags.list', 'mindmap.get', 'note.get?note_id=missing'):
            self.assertEqual(self.request('/api/op/' + path)[0], 400)
        self.conn.execute("INSERT INTO personal_notes(id,text,original_text,tags_json,created_at,updated_at) "
                          "VALUES('loose','遗留想法','遗留想法','[]',?,?)", (NOW,NOW))
        code, pool, _ = self.request('/api/op/unassigned.list')
        self.assertEqual((code, pool['counts']['notes']), (200, 1))
        code, hidden, _ = self.request('/api/op/note.get?project=demo&note_id=loose')
        self.assertEqual((code, hidden['error_code']), (404, 'not_visible'))
        args = dict(project='demo', note_ids=['loose'], root_ids=[], expected_version=pool['version'])
        code, claimed, _ = self.request('/api/op/unassigned.claim', args)
        self.assertEqual((code, claimed['claimed'], claimed['remaining']['notes']), (200, {'notes':1,'trees':0}, 0))
        code, stale, _ = self.request('/api/op/unassigned.claim', args)
        self.assertEqual((code, stale['error_code']), (409, 'version_conflict'))
        code, detail, _ = self.request('/api/op/note.get?project=demo&note_id=loose')
        self.assertEqual(code, 200)
        code, listing, _ = self.request('/api/op/notes.list?project=demo&include_archived=1')
        self.assertEqual(set(listing), {'notes','stale_days','model_calls'})
        self.assertEqual(listing['notes'], [detail['note']])
        code, missing, _ = self.request('/api/op/notes.list?project=missing')
        self.assertEqual((code, missing['error_code']), (404, 'project_unavailable'))

    def test_prompt_settings_are_versioned_and_save_without_starting_execution(self):
        code, config, _ = self.request('/api/op/prompt.list?scope=project&identity=demo')
        self.assertEqual(code, 200, config)
        item = next(row for row in config['items'] if row['key'] == 'summary')
        body = {'scope': 'project', 'identity': 'demo', 'key': 'summary',
                'expected_version': item['version'], 'content': '先写结论，再列尚未验证的内容。'}
        code, saved, _ = self.request('/api/op/prompt.save', body)
        self.assertEqual(code, 200, saved)
        self.assertFalse(saved['started'])
        self.assertEqual(self.processes, [])
        code, history, _ = self.request('/api/op/prompt.history?scope=project&identity=demo&key=summary')
        self.assertEqual(code, 200, history)
        self.assertEqual(len(history['history']), 1)
        code, _, _ = self.request('/api/op/prompt.save', {**body, 'content': '旧版本不能覆盖别人刚保存的修改'})
        self.assertEqual(code, 409)


class MindmapHttpTests(ShellFixture, unittest.TestCase):
    """The mind map's four operations are wired into the real backend's route tables."""
    request = AssistantHttpTests.request
    close_server = AssistantHttpTests.close_server

    def setUp(self):
        ShellFixture.setUp(self)
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=lambda: [], claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=lambda: store.parse_iso(NOW), process_factory=self.process_factory)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .02})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.url = 'http://127.0.0.1:%d' % self.server.server_port

    def test_snapshot_edit_preview_and_convert_are_served(self):
        code, snapshot, _ = self.request('/api/op/mindmap.get?project=demo')
        self.assertEqual(code, 200, snapshot)
        self.assertEqual(snapshot['version'], 0)
        code, edited, _ = self.request('/api/op/mindmap.edit', {'project': 'demo',
            'ops': [{'op': 'create', 'id': 'root-topic', 'label': '下一版', 'parent_id': None}], 'base_version': 0})
        self.assertEqual(code, 200, edited)
        self.assertEqual(edited['map']['version'], 1)
        code, stale, _ = self.request('/api/op/mindmap.edit', {'project': 'demo',
            'ops': [{'op': 'label', 'id': 'root-topic', 'label': '旧版本'}], 'base_version': 0})
        self.assertEqual((code, stale['error_code']), (409, 'version_conflict'))
        # A pre-migration receipt could carry the entire HOME map in the response.
        import hashlib
        old_args = dict(project='demo', roots=['root-topic'], parent_id=None, decisions={}, names={}, leaders={})
        old_hash = hashlib.sha256(json.dumps(['mindmap.preview', old_args], ensure_ascii=False, sort_keys=True,
                                             separators=(',', ':')).encode()).hexdigest()
        self.conn.execute("INSERT INTO op_requests(caller_key,request_id,op,args_hash,status,result,created_at) "
                          "VALUES('human','old-preview','mindmap.preview',?,'done',?,?)",
                          (old_hash, '{"text":"FOREIGN_HISTORY"}', NOW))
        code, conflict, _ = self.request('/api/op/mindmap.preview', old_args, request_id='old-preview')
        self.assertEqual((code, conflict['error_code']), (409, 'request_conflict'))
        self.assertNotIn('FOREIGN_HISTORY', str(conflict))
        convert = dict(project='demo', roots=['root-topic'], parent_id=None, decisions={}, names={}, leaders={},
                       map_version=1, board_version=0, request_id='convert-map')
        code, result, _ = self.request('/api/op/mindmap.convert', convert, request_id='convert-map')
        self.assertEqual((code, result['map']['version']), (200, 2))
        code, replay, _ = self.request('/api/op/mindmap.convert', convert, request_id='convert-map')
        self.assertEqual((code, replay['replayed'], replay['created']), (200, True, result['created']))
        for path in ('/api/op/mindmap.preview', '/api/op/mindmap.convert'):
            code, refused, _ = self.request(path, {})
            self.assertEqual(code, 400, (path, refused))
