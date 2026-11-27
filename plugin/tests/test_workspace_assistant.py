"""Workspace-bound identity, authority and approval replay through normal operation entry points."""
import tests
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import threading
import unittest
from unittest import mock

import operations_expected
from crew import (behavior_prompts, board, domains, mindmap, personal, rules, session_library, session_ops, store,
                  war_room, web, workspace_assistant, workspace_settings)
from crew.ops import confirmations, executor, tokens
from crew.ops.errors import OpError
from ops_helpers import ai_call
from test_campaign_launch import plan
from test_shell import NOW, PLUGIN, ShellFixture


class WorkspaceAssistantTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.other = self.root / 'other'
        self.other.mkdir()
        board.init_project(self.conn, 'other', self.other)
        self.service = web.Service(self.home, PLUGIN, cwd=self.project, agents_provider=lambda: [],
                                   process_factory=self.process_factory, isolated_sources=True)
        self.addCleanup(self.service.close)

    def assistant(self, project='demo'):
        return workspace_assistant.ensure(self.conn, self.home, project)['session_id']

    def test_concurrent_open_same_name_member_and_lifecycle(self):
        board.join(self.conn, 'ordinary', 'demo', '助手', self.project, None, None)
        barrier = threading.Barrier(5)
        def open_page(_):
            with closing(store.connect(self.home)) as conn:
                barrier.wait(timeout=10)
                return workspace_assistant.ensure(conn, self.home, 'demo')['session_id']
        with ThreadPoolExecutor(max_workers=5) as workers:
            identities = set(workers.map(open_page, range(5)))
        self.assertEqual(identities, {self.assistant()})
        sid = identities.pop()
        self.assertNotEqual(sid, self.assistant('other'))
        self.assertEqual(self.processes, [])
        self.assertEqual(board.named_session(self.conn, 'demo', '助手')['session_id'], 'ordinary')
        board.apply_hook(self.conn, 'session-start', {'session_id': sid, 'cwd': str(self.project / 'subdir')},
                         'demo', {'name': '助手', 'role': 'member'}, (None, None))
        saved = board.session(self.conn, sid)
        self.assertEqual((saved['entrypoint'], saved['cwd']), ('crew-workspace-assistant', store.norm_path(self.project)))
        item = session_library.get(self.service, self.conn, 'demo', 'crew:' + sid)
        self.assertEqual((item['category'], item['adoptable']), ('workspace_assistant', False))
        for action in (lambda: session_library.adopt(self.service, self.conn, 'demo', 'crew:' + sid, 'x', 'lead', NOW),
                       lambda: board.roster(self.conn, 'demo', 'move', 'lead', session_ids=[sid]),
                       lambda: session_ops.archive(self.conn, sid), lambda: session_ops.delete(self.conn, sid)):
            with self.assertRaisesRegex(board.Conflict, '专用会话'):
                action()
        self.assertNotIn(sid, [s['session_id'] for s in board.status(self.conn, 'demo', [])['ungrouped']])
        only = self.assistant('other')
        self.assertTrue(self.service.maintenance.cleanup_preview('other')['empty'])
        new = self.root / 'new'
        new.mkdir()
        workspace_settings.apply_update(self.conn, 'other', None, str(new), 0)
        self.assertEqual(store.norm_path(board.session(self.conn, only)['cwd']), store.norm_path(new))

    def test_issued_token_exactly_matches_allowlist_and_scoped_state(self):
        sid, other = self.assistant(), self.assistant('other')
        token = tokens.issue(self.conn, session_id=sid, runtime='claude', now=NOW)
        caller, own = tokens.caller(self.conn, token)
        self.assertEqual(own, operations_expected.WORKSPACE_ASSISTANT)
        row = self.conn.execute('SELECT * FROM mcp_tokens WHERE token_hash=?', (tokens.digest(token),)).fetchone()
        self.assertEqual(json.loads(row['delegable_scopes']), [])
        self.assertEqual(caller.role, 'workspace_assistant')
        who = executor.call(self.service, caller, 'session.whoami', {}, grants=own)
        self.assertEqual((who['kind'], who['project']), ('workspace_assistant', 'demo'))
        self.assertIn('leader.create', who['write_actions'])
        state = executor.call(self.service, caller, 'workspace.state', {}, grants=own)
        self.assertEqual([p['name'] for p in state['projects']], ['demo'])
        self.assertEqual(state['scope'], 'workspace')
        with self.assertRaisesRegex(OpError, '不能调用'):
            executor.call(self.service, caller, 'interaction.resolve', {}, grants=own)
        self.service.chat.journal(other).message('belongs-to-other', '同意新建')
        with self.assertRaisesRegex(board.BoardError, '原始用户消息'):
            ai_call(self.service, self.conn, sid, 'leader.create', {'message': '建立新组'}, message_id='belongs-to-other')
        self.service.chat.journal(sid).message('own-user', '请新建组')
        with mock.patch('crew.ops.launch.endpoint', return_value=('http://127.0.0.1:1/mcp', 'core')):
            leader = ai_call(self.service, self.conn, sid, 'leader.create', {'message': '建立新组'}, message_id='own-user')
        child = self.conn.execute('SELECT * FROM mcp_tokens WHERE session_id=?', (leader['session_id'],)).fetchone()
        self.assertIsNone(child['issuer_token_hash'])
        self.assertEqual(set(json.loads(child['own_scopes'])), tokens.default_scopes('leader'))

    def test_project_rejections_leave_database_unchanged(self):
        sid, other = self.assistant(), self.assistant('other')
        board.join(self.conn, 'foreign', 'other', '外组', self.other, None, None, role='leader')
        foreign = war_room.start(self.conn, 'other', other, '外区', '目标', ['外组'])
        local = war_room.start(self.conn, 'demo', sid, '本区', '目标', ['lead'])
        fid, rid = foreign['id'], local['id']
        post = foreign['first_message']['id']
        item = war_room.set_item(self.conn, fid, other, title='外区事项')['id']
        domains.apply(self.conn, 'other', [{'op': 'create', 'id': 'other01', 'name': '外区', 'x': 0, 'y': 0}], 0)
        operations = [
            ('workspace.state', {'project': 'other'}),
            ('campaign.launch', {'data': {'project': 'other'}}),
            ('leader.merge', {'leader': 's3', 'into': 'foreign', 'handoff': None, 'board_version': 0, 'revision': 'r'}),
            ('war_room.budget', {'room': fid, 'budget_tokens': 100000}),
            ('war_room.get', {'id': fid}),
            ('war_room.post', {'room': rid, 'body': 'reply', 'reply_to': post}),
            ('war_room.item', {'room': rid, 'id': item, 'owner': 'lead'}),
            ('war_room.item', {'room': rid, 'title': 'item', 'owner': 'foreign'}),
            ('campaign.preview', plan(parts=[], leaders=[{'name': '组', 'brief': '做事', 'part': 'other01'}])),
        ]
        first = {'type': 'part.create', 'ref': 'local01', 'name': '新节点', 'parent': None}
        for change in ({'type': 'part.move', 'part': 'local01', 'parent': 'other01'},
                       {'type': 'leader.retire', 'leader': 's3', 'successor': 'foreign'}):
            operations.append(('domains.restructure.apply', {'changes': [first, change], 'note': '改组',
                               'revision': 'r', 'board_version': 0, 'handoffs': []}))
        before = list(self.conn.iterdump())
        for name, args in operations:
            with self.subTest(op=name, args=args), self.assertRaises(OpError) as raised:
                ai_call(self.service, self.conn, sid, name, args)
            self.assertEqual(raised.exception.code, 'not_visible')
            self.assertEqual(list(self.conn.iterdump()), before)

    def test_idea_operations_reject_foreign_references_before_receipts(self):
        sid = self.assistant()
        self.service.chat.journal(sid).message('user-note', '请整理本区想法和导图')
        def act(name, args, **kwargs):
            return ai_call(self.service, self.conn, sid, name, args, message_id='user-note', **kwargs)
        local = act('note.capture', {'data': {'text': '本区'}})['result']['note']['id']
        foreign = personal.note_action(self.conn, 'capture', {'text':'外区','tags':['外区独有']},
                                       project='other', request_id='other-note')['note']['id']
        self.conn.execute("INSERT INTO personal_notes(id,text,original_text,tags_json,created_at,updated_at) "
                          "VALUES('legacy','遗留','遗留','[]',?,?)", (NOW,NOW))
        act('mindmap.edit', dict(base_version=0, ops=[dict(op='create', id='local01', note_id=local, parent_id=None)]))
        mindmap.apply(self.conn, [dict(op='create', id='other01', note_id=foreign, parent_id=None)], 0, project='other')
        domains.apply(self.conn, 'other', [dict(op='create', id='partother', name='外区', x=0, y=0)], 0)
        board.join(self.conn, 'other-chief', 'other', '外组', self.other, None, None, role='leader')
        preview = dict(roots=['local01'], parent_id=None, names={}, decisions={}, leaders={})
        cases = [
            ('note.get', dict(note_id=foreign)),
            ('note.start', dict(note_id=foreign)),
            ('note.recovery.get', dict(note_id=foreign)),
            ('note.update', dict(data=dict(note_id=local, project='other', text='错误', expected_version=1))),
            ('tag.recolor', dict(data=dict(name='外区独有', color='c2', expected_version=1))),
            ('mindmap.edit', dict(base_version=1, ops=[dict(op='collapse', id='local01', collapsed=True),
                                                       dict(op='move', id='local01', parent_id='other01')])),
            ('mindmap.edit', dict(base_version=1, ops=[dict(op='create', note_id=foreign, parent_id=None)])),
            ('mindmap.preview', {**preview, 'roots':['other01']}),
            ('mindmap.preview', {**preview, 'parent_id':'partother'}),
            ('mindmap.convert', {**preview, 'leaders':{'local01':'other-chief'}, 'map_version':1, 'board_version':0}),
            ('note.bulk', dict(data=dict(note_ids=[local], expected_versions={local:1,foreign:1}, op='archive'))),
        ]
        for bad in (foreign, 'legacy'):
            cases.extend([
                ('note.merge', dict(data=dict(primary_id=local, note_ids=[bad], expected_versions={local:1,bad:1}))),
                ('note.bulk', dict(data=dict(note_ids=[local,bad], expected_versions={local:1,bad:1}, op='archive'))),
            ])
        before = list(self.conn.iterdump())
        for name, args in cases:
            with self.subTest(operation=name, args=args):
                with self.assertRaises(OpError) as denied:
                    act(name, args)
                self.assertEqual(denied.exception.code, 'not_visible')
                self.assertEqual(list(self.conn.iterdump()), before)
        self.assertEqual([n['id'] for n in act('notes.list', {})['notes']], [local])
        self.assertEqual(act('mindmap.get', {})['project'], 'demo')
        result = act('note.update', dict(data=dict(note_id=local, text='已编辑', expected_version=1)), request_id='edit-own')
        self.assertEqual(result['result']['note']['version'], 2)
        self.assertTrue(act('note.update', dict(data=dict(note_id=local, text='已编辑', expected_version=1)),
                            request_id='edit-own')['replayed'])

    def test_start_requires_own_verified_message_and_preserves_it_in_leader_instruction(self):
        sid = self.assistant()
        identity = personal.note_action(self.conn, 'capture', {'text':'讨论这条'},
                                        project='demo', request_id='note')['note']['id']
        def start(message=None):
            return ai_call(self.service, self.conn, sid, 'note.start', {'note_id':identity}, message_id=message)
        with self.assertRaises(OpError):
            start()
        self.service.chat.journal(sid).message('user-start', '请从这条想法开始讨论')
        with mock.patch.object(self.service.chat, '_personal_leader', return_value={'session_id':'s3'}) as port:
            result = start('user-start')
            self.assertEqual(result['session_id'], 's3')
            proof = port.call_args.kwargs['instruction']
            self.assertEqual((proof['instruction'], proof['context']['message_id'], proof['context']['session_id']),
                             ('请从这条想法开始讨论', 'user-start', sid))
            self.assertEqual(proof['context']['note']['id'], identity)
            self.assertTrue(start('user-start')['duplicate'])
            self.assertEqual(port.call_count, 1)

    def test_admin_project_and_pool_boundaries(self):
        admin = personal.ensure_assistant(self.conn, self.home)['session_id']
        sid = self.assistant()
        self.service.chat.journal(admin).message('admin-note', '请分别记在这两个区')
        self.conn.execute("INSERT INTO personal_notes(id,text,original_text,tags_json,created_at,updated_at) "
                          "VALUES('loose','遗留池','遗留池','[]',?,?)", (NOW,NOW))
        for name, args in [('notes.list', {}), ('tags.list', {}), ('mindmap.get', {}),
                           ('note.capture', {'data': {'text': '漏写工作区'}})]:
            with self.assertRaises(OpError) as missing:
                ai_call(self.service, self.conn, admin, name, args, message_id='admin-note')
            self.assertEqual(missing.exception.code, 'invalid_arguments')
        for project in ('demo', 'other'):
            args = dict(project=project, data=dict(text=project))
            result = ai_call(self.service, self.conn, admin, 'note.capture', args, message_id='admin-note')
            self.assertEqual(result['result']['note']['project'], project)
        overview = ai_call(self.service, self.conn, admin, 'workspace.state')
        self.assertEqual({n['text'] for n in overview['notes']}, {'demo', 'other'})
        with self.assertRaises(OpError):
            personal.discussion_context(self.conn, 'loose')
        with self.assertRaises(OpError):
            personal.record_discussion(self.conn, 'loose', admin, 'bad', 3)
        for caller in (admin, sid):
            for name in ('unassigned.list', 'unassigned.claim'):
                with self.assertRaises(OpError) as denied:
                    ai_call(self.service, self.conn, caller, name)
                self.assertEqual(denied.exception.code, 'forbidden')

    def test_removed_project_invalidates_token_and_approval_restores_original_session(self):
        sid = self.assistant()
        token = tokens.issue(self.conn, session_id=sid, runtime='claude', now=NOW)
        card = ai_call(self.service, self.conn, sid, 'campaign.launch', plan(board_version=0))
        rid = card['confirmation_id']
        for project, expected in ((None, [rid]), ('demo', [rid]), ('other', [])):
            args = {'project': project} if project else {}
            result = executor.human_call(self.service, 'interaction.list', args)
            self.assertEqual([r['id'] for r in result['requests']], expected)
        with self.assertRaises(OpError) as denied:
            confirmations.resolve(self.service, rid, True, NOW, project='other')
        self.assertEqual(denied.exception.code, 'not_visible')
        workspace_settings.remove(self.conn, 'demo', 0)
        for fn in (lambda: tokens.caller(self.conn, token), lambda: self.assistant(),
                   lambda: executor.human_call(self.service, 'assistant.state', {'project': 'demo'})):
            with self.assertRaises(OpError) as error:
                fn()
            self.assertEqual(error.exception.code, 'project_unavailable')
        result = confirmations.resolve(self.service, rid, True, NOW, project='demo')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.processes, [])
        board.init_project(self.conn, 'demo', self.project)
        self.assertEqual(self.assistant(), sid)
        self.assertTrue(any(rid in e.get('text', '') for e in self.service.chat.history(sid)['events']))

    def test_confirmation_launch_uses_real_lead_and_host_issued_leader_tokens(self):
        sid = self.assistant()
        self.service.chat.journal(sid).message('campaign', '给本区开局')
        checked = ai_call(self.service, self.conn, sid, 'campaign.preview', plan())
        args = plan(board_version=checked['board_version'])
        requested = ai_call(self.service, self.conn, sid, 'campaign.launch', args, request_id='launch')
        repeated = ai_call(self.service, self.conn, sid, 'campaign.launch', {**args, 'project': 'demo'}, request_id='launch')
        self.assertEqual(requested, repeated)
        with mock.patch('crew.ops.launch.endpoint', return_value=('http://127.0.0.1:1/mcp', 'core')):
            result = confirmations.resolve(self.service, requested['confirmation_id'], True, NOW, project='demo')
        self.assertEqual(result['status'], 'approved', result)
        replay = ai_call(self.service, self.conn, sid, 'campaign.launch', args, request_id='launch')
        self.assertEqual((replay['status'], replay['confirmation_id']), ('approved', requested['confirmation_id']))
        room = war_room.get(self.conn, result['result']['room']['id'])
        self.assertEqual(room['lead_session_id'], sid)
        for member in room['members']:
            row = self.conn.execute('SELECT * FROM mcp_tokens WHERE session_id=?', (member['session_id'],)).fetchone()
            self.assertIsNone(row['issuer_token_hash'])
            self.assertEqual(set(json.loads(row['own_scopes'])), tokens.default_scopes('leader'))
        self.assertTrue(any(requested['confirmation_id'] in e.get('text', '') for e in self.service.chat.history(sid)['events']))

    def test_project_behavior_overrides_are_isolated(self):
        sid, other = self.assistant(), self.assistant('other')
        admin = personal.ensure_assistant(self.conn, self.home)['session_id']
        behavior_prompts.save(self.conn, 'workspace_assistant', 'A 区守则', scope='project', identity='demo', expected_version=0)
        behavior_prompts.save(self.conn, 'assistant', '管理员原有覆盖', expected_version=0)
        for who, expected, excluded in ((sid, 'A 区守则', '管理员原有覆盖'), (other, '本工作区的助手', 'A 区守则'),
                                        (admin, '管理员原有覆盖', 'A 区守则')):
            manifest = behavior_prompts.render(behavior_prompts.for_session(self.conn, who, plugin_root=PLUGIN))
            self.assertIn(expected, manifest)
            self.assertNotIn(excluded, manifest)
        self.assertEqual(rules.skill_role(self.conn, board.session(self.conn, sid)), 'workspace_assistant')

    def test_three_room_leads_get_only_their_own_wakeups(self):
        sid, other = self.assistant(), self.assistant('other')
        admin = personal.ensure_assistant(self.conn, self.home)['session_id']
        board.join(self.conn, 'legacy-l', 'demo', '旧组', self.project, None, None, role='leader')
        board.join(self.conn, 'other-l', 'other', '外组', self.other, None, None, role='leader')
        rooms = [(lead, project, member, war_room.start(self.conn, project, lead, title, '目标', [member],
                                                        budget_tokens=100000))
                 for lead, project, member, title in ((sid, 'demo', 's3', '本区房'), (other, 'other', 'other-l', '外区房'),
                                                      (admin, 'demo', 'legacy-l', '旧房'))]
        with self.assertRaises(board.BoardError):
            war_room.set_budget(self.conn, rooms[0][3]['id'], admin, 200000)
        for lead, project, member, room in rooms:
            card = war_room.decide(self.conn, room['id'], lead, '何时开工')
            board.answer(self.conn, card['id'], room['title'] + '答复')
            war_room.task_returned(self.conn, {'project': project, 'leader_session_id': member,
                                               'title': room['title'] + '任务'}, 'awaiting_acceptance')
        with mock.patch.object(war_room, 'spent', return_value=120000):
            self.assertEqual(len(war_room.over_budget(self.conn, NOW)), 3)
        for lead, project, member, room in rooms:
            notices = board.pending_notices(self.conn, lead)
            self.assertEqual(len(notices), 3)
            self.assertTrue(all(note['project'] == project and room['title'] in note['text'] for note in notices))
        self.service.chat.deliver_leader_notices(self.conn, {}, NOW)
        for lead, project, member, room in rooms:
            self.assertEqual(board.pending_notices(self.conn, lead), [])
            messages = [e['text'] for e in self.service.chat.history(lead)['events'] if e['type'] == 'message']
            self.assertEqual(len(messages), 1)
            self.assertIn(room['title'], messages[0])
            for other_lead, _, _, other_room in rooms:
                if other_lead != lead:
                    self.assertNotIn(other_room['title'], messages[0])

    def test_approval_rechecks_board_version_and_object_membership(self):
        sid = self.assistant()
        self.service.chat.journal(sid).set_status('idle')
        card = ai_call(self.service, self.conn, sid, 'campaign.launch', plan(board_version=0))
        domains.apply(self.conn, 'demo', [{'op': 'create', 'id': 'local01', 'name': '本区', 'x': 0, 'y': 0}], 0)
        outcome = confirmations.resolve(self.service, card['confirmation_id'], True, NOW)
        self.assertEqual(outcome['status'], 'failed')
        self.assertIn('重新预览', outcome['error'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM war_rooms').fetchone()[0], 0)
        changes = [{'type': 'part.rename', 'part': 'local01', 'name': '新名'}]
        checked = ai_call(self.service, self.conn, sid, 'domains.restructure.preview', {'changes': changes})
        card = ai_call(self.service, self.conn, sid, 'domains.restructure.apply',
                       {'changes': changes, 'note': '改名', 'board_version': checked['board_version'],
                        'revision': checked['revision'], 'handoffs': []})
        self.conn.execute("UPDATE domains SET project='other' WHERE id='local01'")
        outcome = confirmations.resolve(self.service, card['confirmation_id'], True, NOW)
        self.assertEqual(outcome['status'], 'failed')
        self.assertEqual(self.conn.execute("SELECT name FROM domains WHERE id='local01'").fetchone()[0], '本区')

    def test_a_pending_card_has_one_decision_and_one_reply(self):
        sid = self.assistant()
        self.service.chat.journal(sid).set_status('idle')
        rid = ai_call(self.service, self.conn, sid, 'campaign.launch', plan(board_version=0))['confirmation_id']
        start = threading.Barrier(2)
        def decide():
            start.wait(timeout=10)
            try:
                return confirmations.resolve(self.service, rid, False, NOW, project='demo')['status']
            except OpError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(decide) for _ in range(2)]
            outcomes = [future.result() for future in futures]
        self.assertEqual(outcomes.count('rejected'), 1)
        self.assertTrue(set(outcomes) <= {'rejected', 'request_conflict', 'conflict'})
        messages = [e for e in self.service.chat.history(sid)['events'] if rid in e.get('text', '')]
        self.assertEqual(len(messages), 1)
        next_card = ai_call(self.service, self.conn, sid, 'campaign.launch', plan(board_version=0))
        self.service.chat.stop_identity(sid)
        next_id = next_card['confirmation_id']
        confirmations.resolve(self.service, next_id, False, NOW, project='demo')
        self.assertFalse(self.service.chat.running(sid))
        self.assertTrue(any(next_id in notice['text'] for notice in board.pending_notices(self.conn, sid)))
