"""Quiet remote reminders and assistant replies, with isolated homes and fake carriers."""
import tests
import contextlib
import datetime as dt
import json
import unittest
from unittest import mock

from crew import (behavior_prompts, board, chat, codex_tools, decision_briefs, goal_actions, goal_links, goal_reports, goal_runtime, goals,
                  notifications, personal, personal_actions, session_ops, store, todo,
                  web, workspace_settings)
from test_goals import GoalFixture
from test_notifications import FakeQQ
from test_shell import NOW, PLUGIN, ShellFixture


FOOTER = '直接回复这里，全局助手会处理。'
UNAVAILABLE = '全局助手未启用，请先在 Crew 打开全局助手。'


class RemoteQuietTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.clock = store.parse_iso(NOW)
        self.clients = {channel: FakeQQ() for channel in ('qq', 'lark')}
        for channel in self.clients:
            (self.home / (channel + '.json')).write_text(json.dumps({
                'app_id': 'fake-app', 'app_secret': 'fake-secret'}), encoding='utf-8')
            filename = 'notify.json' if channel == 'qq' else 'lark-notify.json'
            (self.home / filename).write_text(json.dumps({
                'owner_openid': 'owner', 'since': NOW, 'manual_away': False}), encoding='utf-8')
        self.service = web.Service(self.home, PLUGIN, cwd=self.project, agents_provider=lambda: [],
            claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
            now=lambda: self.clock, notification_client=self.clients['qq'],
            lock_status=lambda: False,
            process_factory=self.process_factory, vscode_state_db=self.vscode_state_db)
        self.addCleanup(self.service.close)
        self.inboxes = self.service.connections.inboxes
        self.inboxes['lark'].client = self.clients['lark']
        self.counter = 0

    def receive(self, channel, text, ref=None, images=()):
        self.counter += 1
        event = dict(message_id='input-%s' % self.counter, openid='owner', text=text,
                     quoted_ref=ref, attachments=list(images))
        self.inboxes[channel].receive('message', event)
        return event

    def assistant(self):
        return personal.ensure_assistant(self.conn, self.home, selection={
            'runtime': 'claude', 'model': None, 'effort': None})

    def test_four_reminder_kinds_merge_into_one_plain_text_with_individual_refs(self):
        question = '决' * 61 + '\n不应另起一行'
        number = board.ask(self.conn, 's1', question, ['A', 'B'], now=NOW)
        action = todo.add(self.conn, 's1', '操' * 61, 'SECRET STEPS', 'SECRET VERIFY', now=NOW)
        board.report(self.conn, 's3', 'SECRET PROGRESS', now=NOW)
        errors = [dict(id=kind, kind=kind, session_id='s1', title='错' * 61,
                       detail='SECRET ERROR DETAIL', created_at=NOW)
                  for kind in ('session_error', 'codex_failed')]
        state = self.service.state
        with mock.patch.object(self.service, 'state', side_effect=lambda project: {
                **state(project), 'attention': state(project)['attention'] + errors}):
            for channel, inbox in self.inboxes.items():
                with self.subTest(channel=channel):
                    inbox.set_away(True)
                    inbox.tick()
                    self.assertEqual(len(self.clients[channel].sent), 1)
                    message = self.clients[channel].sent[0]
                    self.assertEqual(message['kind'], 'text')
                    self.assertIsNone(message['keyboard'])
                    self.assertEqual(message['text'].splitlines(), [
                        '【待拍板】demo·lead：' + '决' * 60 + '…',
                        '【卡住】demo·lead：' + '错' * 60 + '…',
                        '【卡住】demo·lead：' + '错' * 60 + '…',
                        '【TODO】demo·lead：' + '操' * 60 + '…', FOOTER])
                    rows = inbox.execute(self.conn, 'SELECT * FROM notification_messages').fetchall()
                    self.assertEqual(len(rows), 4)
                    self.assertEqual({row['message_id'] for row in rows}, {message['result']['id']})
                    self.assertEqual({row['ref_idx'] for row in rows}, {message['result']['ref_idx']})
                    self.assertEqual({row['status'] for row in rows}, {'sent'})
                    self.assertEqual({row['kind'] for row in rows}, {'decision', 'todo', 'session_error', 'codex_failed'})
                    inbox.tick()
                    self.assertEqual(len(self.clients[channel].sent), 1)
        self.assertEqual(board.decisions(self.conn, 'demo')[0]['id'], number)
        self.assertEqual(todo.get(self.conn, 'demo', action['id'])['status'], 'open')

    def test_report_completion_decision_and_todo_share_one_channel_message(self):
        target = goals.create(self.conn, 'demo', 's3', 'Deliver integration', 'User request', ['Verify integration'], now=NOW)
        goals.transition(self.conn, target['id'], 'running', now=NOW)
        report = goals.save_report(self.conn, target['id'], dict(
            outcome='awaiting_acceptance', summary='SECRET REPORT SUMMARY',
            user_test=[dict(step='Open result', expected='Works')],
            user_todos=[dict(title='Connect device', detail='SECRET STEPS')],
            user_decisions=[dict(question='Release when?', why='SECRET WHY')],
            acceptance=[dict(criterion_id='a1', status='unverified', evidence=[])],
            examples=[dict(kind='actual', input='x', expected='y', actual='y', conclusion='measured')],
            investment=dict(elapsed_seconds=None, usage=None, roles=[]),
            document_html='<html><body>Integration report</body></html>'), now=NOW)
        goals.request_finish(self.conn, target['id'], report['id'], actor='s3', now=NOW)
        goals.finalize_finish(self.conn, target['id'], now=NOW)
        for channel, inbox in self.inboxes.items():
            with self.subTest(channel=channel):
                inbox.set_away(True)
                inbox.tick()
                self.assertEqual(len(self.clients[channel].sent), 1)
                message = self.clients[channel].sent[0]
                self.assertEqual(message['text'].splitlines()[-2:], [
                    '【完成】demo·lead：Deliver integration 待你验收', FOOTER])
                self.assertEqual(len(message['text'].splitlines()), 4)
                self.assertNotIn('SECRET', message['text'])
                rows = inbox.execute(self.conn, 'SELECT * FROM notification_messages').fetchall()
                self.assertEqual({r['kind'] for r in rows}, {'goal_report', 'todo', 'decision'})
                self.assertEqual({r['message_id'] for r in rows}, {message['result']['id']})
                self.assertEqual({r['ref_idx'] for r in rows}, {message['result']['ref_idx']})

    def test_retired_stage_in_legacy_mixed_retry_batch_prevents_old_payload_replay(self):
        for channel, inbox in self.inboxes.items():
            with self.subTest(channel=channel):
                identities = ['legacy-stage', 'legacy-error']
                payload = json.dumps(dict(identities=identities, method='text', args=['OLD STAGE BODY'],
                                          recipient=inbox._recipient()))
                for identity, kind in zip(identities, ('report', 'session_error')):
                    inbox.record_message(self.conn, identity, dict(workspace='demo', leader_session_id='s3',
                        decision=None, item={'id': identity, 'kind': kind}), kind, NOW)
                    inbox.execute(self.conn, "UPDATE notification_messages SET status='retry',attempts=1,next_attempt=?,delivery_json=? WHERE item_id=?",
                                  (self.clock.timestamp(), payload, identity))
                replacement = notifications.Inbox(self.service, self.clients[channel],
                    lock_status=lambda: False, channel_id=channel)
                self.addCleanup(replacement.close)
                replacement.set_away(True)
                replacement.tick()
                self.assertEqual({r[0] for r in replacement.execute(self.conn, 'SELECT status FROM notification_messages')}, {'retired'})
                self.assertEqual(self.clients[channel].sent, [])

    def test_decision_brief_uses_title_and_never_pushes_background_or_options(self):
        board.ask(self.conn, 's1', 'Original question', recommendation='Choose A', brief={
            'title': 'Brief title', 'context': 'SECRET BACKGROUND', 'why_user': 'SECRET WHY',
            'recommendation_reason': 'SECRET RECOMMENDATION', 'impact': 'SECRET IMPACT',
            'options': [{'label': 'Choose A', 'consequence': 'SECRET A'},
                        {'label': 'Choose B', 'consequence': 'SECRET B'}]}, now=NOW)
        inbox = self.inboxes['qq']
        inbox.set_away(True)
        inbox.tick()
        self.assertEqual(self.clients['qq'].sent[-1]['text'], '【待拍板】demo·lead：Brief title\n' + FOOTER)
        self.receive('qq', '/decisions')
        message = self.clients['qq'].sent[-1]
        self.assertIsNotNone(message['keyboard'])
        for part in ('SECRET BACKGROUND', 'SECRET WHY', 'SECRET IMPACT', 'SECRET A', 'SECRET B'):
            self.assertIn(part, message['text'])

    def test_reply_quotes_all_batch_items_and_sends_to_assistant_with_channel_source(self):
        assistant = self.assistant()
        board.ask(self.conn, 's1', 'Approve?', ['Yes'], now=NOW)
        todo.add(self.conn, 's1', 'Connect', 'Cable', 'Ready', now=NOW)
        for channel, inbox in self.inboxes.items():
            with self.subTest(channel=channel):
                inbox.set_away(True)
                inbox.tick()
                message = self.clients[channel].sent[-1]
                rows = inbox.execute(self.conn, 'SELECT * FROM notification_messages').fetchall()
                event = self.receive(channel, 'Yes, and connected', message['result']['ref_idx'])
                self.inboxes[channel].receive('message', event)
                queued = inbox.execute(self.conn, 'SELECT * FROM notification_replies').fetchall()
                self.assertEqual(len(queued), 1)
                self.assertEqual(queued[0]['session_id'], assistant['session_id'])
                text = json.loads(queued[0]['text'])['text']
                for part in ('宿主', 'decision', 'todo', 'demo', 'lead', 'session_id=s3',
                             'decision_id=' + str(rows[0]['decision_id']), 'todo_id=1', 'Yes, and connected'):
                    self.assertIn(part, text)
                journal = self.service.chat.journal(assistant['session_id'])
                def send(session, body, *, identity, source, images):
                    self.assertEqual(session['session_id'], assistant['session_id'])
                    self.assertEqual(source, channel)
                    journal.message(identity, body, source)
                    journal.set_status('working')
                with mock.patch.object(self.service.chat, 'send', side_effect=send) as port:
                    inbox.process_replies()
                    port.assert_called_once()
                journal.emit('assistant', id='answer-' + channel, content=[dict(type='text', text='Recorded by assistant')])
                journal.set_status('idle')
                inbox.process_replies()
                self.assertIn('Recorded by assistant', self.clients[channel].sent[-1]['text'])
                self.assertEqual(board.decisions(self.conn, 'demo')[0]['status'], 'open')
                self.assertEqual(board.pending_notices(self.conn, 's3'), [])

    def test_assistant_reply_interruption_keeps_sending_without_message_receipts_or_replay(self):
        assistant = self.assistant()
        for channel, inbox in self.inboxes.items():
            with self.subTest(channel=channel):
                self.receive(channel, 'Long answer please')
                journal = self.service.chat.journal(assistant['session_id'])
                def send(session, text, *, identity, source, images):
                    journal.message(identity, text, source)
                    journal.set_status('working')
                with mock.patch.object(self.service.chat, 'send', side_effect=send):
                    inbox.process_replies()
                journal.emit('assistant', id='long-' + channel,
                             content=[dict(type='text', text='A' * notifications.TEXT_LIMIT + 'B' * 20)])
                journal.set_status('idle')
                client = self.clients[channel]
                real_send = client.send_markdown
                calls = []
                def interrupt(owner, text):
                    calls.append(text)
                    if len(calls) == 2:
                        raise notifications.qq.QQError('lost after first fragment', kind='network')
                    return real_send(owner, text)
                with mock.patch.object(client, 'send_markdown', side_effect=interrupt):
                    with self.assertRaisesRegex(notifications.qq.QQError, 'lost after first fragment'):
                        inbox.process_replies()
                self.assertEqual(inbox.execute(self.conn, 'SELECT status FROM notification_replies').fetchone()[0], 'sending')
                self.assertEqual(inbox.execute(self.conn, 'SELECT COUNT(*) FROM notification_messages').fetchone()[0], 0)
                sent = len(client.sent)
                replacement = notifications.Inbox(self.service, client, lock_status=lambda: False, channel_id=channel)
                self.addCleanup(replacement.close)
                replacement.process_replies()
                self.assertEqual(len(client.sent), sent)
                self.assertIn('未完成', replacement.error)
                # An ordinary assistant reply has no reminder receipt to inject.
                self.receive(channel, 'Continue', client.sent[-1]['result']['ref_idx'])
                row = inbox.execute(self.conn, "SELECT text FROM notification_replies WHERE status='queued'").fetchone()
                self.assertEqual(json.loads(row['text'])['text'], 'Continue')

    def test_real_chat_first_send_prepares_assistant_and_keeps_channel_images(self):
        assistant = self.assistant()
        for channel, inbox in self.inboxes.items():
            with self.subTest(channel=channel):
                client = self.clients[channel]
                client.files['https://fake.invalid/image'] = b'image'
                self.receive(channel, 'Check picture', images=[dict(kind='image', url='https://fake.invalid/image',
                    content_type='image/png', filename='shot.png', size=5, voice_text=None)])
                with mock.patch('crew.chat.claude_executable', return_value='fake-claude'):
                    inbox.process_replies()
                journal = self.service.chat.journal(assistant['session_id'])
                message = next(event for event in reversed(journal.events) if event['type'] == 'message')
                self.assertEqual(message['source'], channel)
                self.assertEqual(message['text'], 'Check picture')
                self.assertEqual(len(message['images']), 1)
                journal.emit('assistant', id='done-' + channel, content=[dict(type='text', text='Checked')])
                journal.set_status('idle')
                inbox.process_replies()
                self.assertEqual(client.sent[-1]['text'], 'Checked')
                self.assertEqual(inbox.execute(self.conn, 'SELECT COUNT(*) FROM notification_messages').fetchone()[0], 0)

    def test_assistant_unavailable_never_queues_for_leader(self):
        assistant = None
        for state in ('missing', 'closed', 'archived'):
            if state != 'missing':
                if assistant is None:
                    assistant = self.assistant()
                sid = assistant['session_id']
                if state == 'closed':
                    self.conn.execute("UPDATE sessions SET status='closed' WHERE session_id=?", (sid,))
                else:
                    self.conn.execute("UPDATE sessions SET status='idle' WHERE session_id=?", (sid,))
                    self.conn.execute("INSERT INTO session_controls(session_id,archived_status) VALUES(?,'idle')", (sid,))
            for channel, inbox in self.inboxes.items():
                with self.subTest(state=state, channel=channel):
                    self.receive(channel, 'Please handle this')
                    self.assertEqual(self.clients[channel].sent[-1]['text'], UNAVAILABLE)
                    self.assertEqual(inbox.execute(self.conn, 'SELECT COUNT(*) FROM notification_replies').fetchone()[0], 0)

    def test_removed_commands_are_unknown_and_help_describes_assistant(self):
        for channel in self.inboxes:
            for command in ('/leader', '/l', '/1', '/leader 1', '/l lead', '/projects', '/p', '/project', '/project demo'):
                with self.subTest(channel=channel, command=command):
                    self.receive(channel, command)
                    self.assertEqual(self.clients[channel].sent[-1]['text'], '未知命令，请用 /h 查看帮助。')
            self.receive(channel, '/h')
            text = self.clients[channel].sent[-1]['text']
            self.assertIn('全局助手', text)
            for removed in ('/leader', '/projects', '/project', '/1'):
                self.assertNotIn(removed, text)

    def test_error_since_filter_and_resolved_or_held_items_stay_quiet(self):
        number = board.ask(self.conn, 's1', 'Held', ['A'], now=NOW)
        board.hold(self.conn, number, now=NOW)
        action = todo.add(self.conn, 's1', 'Resolved', 'Do', 'Check', now=NOW)
        todo.resolve(self.conn, 'demo', action['id'], 'done')
        items = [dict(id=kind, kind=kind, session_id='s1', title='Old failure', detail='Old',
                      created_at='2026-10-20T00:00:00.000Z') for kind in ('session_error', 'codex_failed')]
        with mock.patch.object(self.service, 'state', return_value={'attention': items}):
            for channel, inbox in self.inboxes.items():
                inbox.set_away(True)
                inbox.tick()
                self.assertEqual(self.clients[channel].sent, [])

    def test_legacy_progress_receipts_are_retired_without_transport_on_restart(self):
        for channel, inbox in self.inboxes.items():
            for kind in ('report', 'goal_progress'):
                for status in ('retry', 'sending', 'error'):
                    key = kind + ':' + status
                    inbox.record_message(self.conn, key, dict(workspace='demo', leader_session_id='s3',
                        decision=None, item={'id': key, 'kind': kind}), kind, NOW)
                    inbox.execute(self.conn, 'UPDATE notification_messages SET status=?,next_attempt=? WHERE item_id=?',
                                  (status, self.clock.timestamp(), key))
            inbox.close()
            replacement = notifications.Inbox(self.service, self.clients[channel],
                                               lock_status=lambda: False, channel_id=channel)
            self.addCleanup(replacement.close)
            replacement.set_away(True)
            replacement.tick()
            rows = replacement.execute(self.conn, 'SELECT status,next_attempt FROM notification_messages').fetchall()
            self.assertEqual(len(rows), 6)
            self.assertEqual({tuple(row) for row in rows}, {('retired', None)})
            self.assertEqual(self.clients[channel].operations, [])


class ReportDecisionTests(GoalFixture):
    def test_report_decisions_validate_optional_fields_and_boundaries(self):
        target = self.goal()
        base = self.report('awaiting_acceptance')
        self.assertEqual(goals.validate_report(self.conn, target['id'], base)['user_decisions'], [])
        valid = [{'question': 'Q' * 500, 'why': 'W' * 2000, 'options': [], 'recommendation': None},
                 {'question': 'Choose?', 'why': 'Your schedule', 'options': ['A', 'B'], 'recommendation': 'A'},
                 {'question': 'Explain?', 'why': 'Your preference'}]
        result = goals.validate_report(self.conn, target['id'], {**base, 'user_decisions': valid})
        self.assertEqual(result['user_decisions'][0], valid[0])
        self.assertEqual(result['user_decisions'][1], valid[1])
        self.assertEqual(result['user_decisions'][2], {**valid[2], 'options': [], 'recommendation': None})
        invalid = [None, {}, ['question'], [{}], [{'question': 'Q', 'why': ' '}],
                   [{'question': ' ', 'why': 'Why'}], [{'question': 'Q' * 501, 'why': 'Why'}],
                   [{'question': 'Q', 'why': 'W' * 2001}], [{'question': 'Q', 'why': 'Why', 'options': None}],
                   [{'question': 'Q', 'why': 'Why', 'options': ['']}],
                   [{'question': 'Q', 'why': 'Why', 'options': [7]}],
                   [{'question': 'Q', 'why': 'Why', 'options': 'A'}],
                   [{'question': 'Q', 'why': 'Why', 'options': ['A'], 'recommendation': 'B'}],
                   [{'question': 'Q', 'why': 'Why', 'options': [], 'recommendation': 1}]]
        for value in invalid:
            with self.subTest(value=value), self.assertRaisesRegex(board.BoardError, 'user_decisions'):
                goals.validate_report(self.conn, target['id'], {**base, 'user_decisions': value})

    def test_finalize_registers_nonblocking_leader_decisions_once(self):
        for actor in ('chief-id', board.HUMAN):
            target = self.goal()
            goals.transition(self.conn, target['id'], 'running')
            decisions = [{'question': 'Release when?', 'why': 'It affects your customers',
                          'options': ['Now', 'Later'], 'recommendation': 'Later'},
                         {'question': 'Which account?', 'why': 'Only you know'}]
            report = goals.save_report(self.conn, target['id'], {
                **self.report('awaiting_acceptance'), 'user_decisions': decisions,
                'user_todos': [{'title': 'Connect', 'detail': 'Cable'}]})
            goals.request_finish(self.conn, target['id'], report['id'], actor=actor)
            goals.finalize_finish(self.conn, target['id'])
            prefix = '来自任务「%s」：' % target['title']
            rows = board.decisions(self.conn, 'demo', all=True)
            self.assertEqual(len(rows), 2 if actor == 'chief-id' else 4)
            for item, expected in zip(rows[-2:], decisions):
                self.assertEqual(item['question'], prefix + expected['question'] + '\n原因：' + expected['why'])
                self.assertEqual(item['session_id'], 'chief-id')
                self.assertEqual(item['status'], 'open')
                self.assertFalse(item['blocking'])
                self.assertEqual(item['options'], expected.get('options', []))
                self.assertEqual(item['recommendation'], expected.get('recommendation'))
            self.assertTrue(goals.finalize_finish(self.conn, target['id'])['already'])
            self.assertEqual(board.decisions(self.conn, 'demo', all=True), rows)
        self.assertEqual(goal_actions.report_template(self.conn, target['id'])['user_decisions'], [])

    def test_failed_decision_registration_rolls_back_todos_and_finish(self):
        target = self.goal()
        goals.transition(self.conn, target['id'], 'running')
        report = goals.save_report(self.conn, target['id'], {
            **self.report('awaiting_acceptance'), 'user_todos': [{'title': 'Connect', 'detail': 'Cable'}],
            'user_decisions': [{'question': 'Release?', 'why': 'User schedule'}]})
        goals.request_finish(self.conn, target['id'], report['id'], actor='chief-id')
        with mock.patch.object(board.ask, '__wrapped__', side_effect=board.BoardError('injected decision failure')):
            with self.assertRaisesRegex(board.BoardError, 'injected decision failure'):
                goals.finalize_finish(self.conn, target['id'])
        self.assertEqual(todo.list(self.conn, 'demo'), [])
        self.assertEqual(board.decisions(self.conn, 'demo'), [])
        self.assertEqual(goals.get_finish(self.conn, target['id'])['status'], 'pending')
        goals.finalize_finish(self.conn, target['id'])
        self.assertEqual(len(todo.list(self.conn, 'demo')), 1)
        self.assertEqual(len(board.decisions(self.conn, 'demo')), 1)


class RemoteDecisionActionTests(GoalFixture):
    def test_verified_original_message_answers_user_controlled_goal_with_true_actor(self):
        assistant = personal.ensure_assistant(self.conn, self.home)
        target = self.goal()
        goals.transition(self.conn, target['id'], 'running')
        execution = goals.create_execution(self.conn, target['id'], 'chief-id', runtime='claude')
        for source, actor in (('qq', 'qq'), ('lark', 'lark'), ('human', board.HUMAN)):
            with self.subTest(source=source):
                number = board.ask(self.conn, 'chief-id', 'Continue?', ['Yes'])
                goal_links.bind(self.conn, 'decision', number, execution['id'])
                with self.assertRaisesRegex(board.BoardError, 'AI 不能代替用户'):
                    board.answer(self.conn, number, 'Yes', actor=source if source != 'human' else 'assistant')
                event = dict(type='message', role='user', source=source, id='original-' + source, text='Yes')
                payload = dict(data={'decision_id': number, 'answer': 'Yes'},
                               message_id=event['id'], request_id='answer-' + source)
                result = personal_actions.dispatch(self.home, assistant['session_id'], 'decision.answer', payload,
                                                   read_events=lambda _: [event])
                self.assertTrue(result['ok'])
                row = self.conn.execute('SELECT status,answer,answered_by FROM decisions WHERE id=?', (number,)).fetchone()
                self.assertEqual(tuple(row), ('answered', 'Yes', actor))
                replay = personal_actions.dispatch(self.home, assistant['session_id'], 'decision.answer', payload,
                                                   read_events=lambda _: [event])
                self.assertTrue(replay['duplicate'])

    def test_assistant_answer_wakes_owning_leader_unless_goal_linked(self):
        assistant = personal.ensure_assistant(self.conn, self.home)
        loose = board.ask(self.conn, 'chief-id', 'Ship it?', ['Yes', 'No'])
        target = self.goal()
        goals.transition(self.conn, target['id'], 'running')
        execution = goals.create_execution(self.conn, target['id'], 'chief-id', runtime='claude')
        linked = board.ask(self.conn, 'chief-id', 'Linked?', ['Yes'])
        goal_links.bind(self.conn, 'decision', linked, execution['id'])
        before = len(board.pending_notices(self.conn, 'chief-id'))
        event = dict(type='message', role='user', source='qq', id='remote-ship', text='发吧')
        result = personal_actions.dispatch(self.home, assistant['session_id'], 'decision.answer',
            dict(data={'decision_id': loose, 'answer': 'Yes'}, message_id=event['id'], request_id='ship'),
            read_events=lambda _: [event])
        self.assertEqual(result['result']['routed_to'], {'leader_session_id': 'chief-id'})
        notices = board.pending_notices(self.conn, 'chief-id')
        self.assertEqual(len(notices), before + 1)
        self.assertIn('答复：Yes', json.dumps(notices, ensure_ascii=False))
        event = dict(event, id='remote-linked')
        result = personal_actions.dispatch(self.home, assistant['session_id'], 'decision.answer',
            dict(data={'decision_id': linked, 'answer': 'Yes'}, message_id=event['id'], request_id='linked'),
            read_events=lambda _: [event])
        self.assertEqual(result['result']['routed_to'], {'goal_id': target['id']})
        self.assertEqual(len(board.pending_notices(self.conn, 'chief-id')), before + 1)

    def test_codex_personal_tool_answers_with_verified_remote_actor(self):
        assistant = personal.ensure_assistant(self.conn, self.home, selection={
            'runtime': 'codex', 'model': None, 'effort': None})
        number = board.ask(self.conn, 'chief-id', 'Release?', ['Yes'])
        event = dict(type='message', role='user', source='lark', id='remote-answer', text='Yes')
        tool = codex_tools.CodexTools(self.home, assistant['session_id'], personal_handler=lambda action, payload, actor:
            personal_actions.dispatch(self.home, actor, action, payload, read_events=lambda _: [event]))
        result = tool({'tool': 'crew_personal', 'arguments': {'action': 'decision.answer',
            'data': {'decision_id': number, 'answer': 'Yes'}, 'message_id': event['id'], 'request_id': 'codex-answer'}})
        self.assertTrue(result['success'], result)
        self.assertEqual(self.conn.execute('SELECT answered_by FROM decisions WHERE id=?', (number,)).fetchone()[0], 'lark')

    def test_effective_prompts_cover_remote_actions_task_split_and_report_review(self):
        assistant = personal.ensure_assistant(self.conn, self.home)
        assistant_rules = behavior_prompts.DEFAULTS['assistant']
        context = personal.assistant_context(self.conn, assistant['session_id'])
        for text in (assistant_rules, context['instructions']):
            for part in ('QQ/Lark', 'decision.answer', 'todo.resolve', 'session.message', '含糊时先回问一句'):
                self.assertIn(part, text)
        leader_rules = behavior_prompts.DEFAULTS['leader']
        for part in ('每件各建一个目标并分别 start', '已有同一任务就补充原目标', 'crew todo list', 'crew decisions', 'user_decisions'):
            self.assertIn(part, leader_rules)
        for part in ('crew todo list', 'crew decisions', 'todo withdraw', '撤回拍板', 'user_todos', 'user_decisions'):
            self.assertIn(part, goal_runtime.BASE_INSTRUCTIONS)

    def test_missing_answered_or_unverified_decisions_are_rejected(self):
        assistant = personal.ensure_assistant(self.conn, self.home)
        number = board.ask(self.conn, 'chief-id', 'Continue?', ['Yes'])
        answered = board.ask(self.conn, 'chief-id', 'Already answered', ['Yes'])
        board.answer(self.conn, answered, 'Yes')
        event = dict(type='message', role='user', source='qq', id='original', text='Yes')
        for identity, events, label in ((999999, [event], '不存在'), (answered, [event], '已处理'),
                                        (number, [], '原始用户消息'),
                                        (number, [{**event, 'source': 'crew'}], '真实用户输入')):
            with self.subTest(identity=identity, events=events), self.assertRaisesRegex(board.BoardError, label):
                personal_actions.dispatch(self.home, assistant['session_id'], 'decision.answer',
                    dict(data={'decision_id': identity, 'answer': 'Yes'}, message_id='original', request_id='invalid'),
                    read_events=lambda _: events)
        self.assertEqual(self.conn.execute('SELECT status FROM decisions WHERE id=?', (number,)).fetchone()[0], 'open')


if __name__ == '__main__':
    unittest.main()
