"""QQ routing uses sent snapshots and stable Crew ids; no real messages or models."""
import tests
import json
import unittest
from unittest import mock

from crew import board, decision_briefs, notifications, runtimes, session_ops, store, web
from test_notifications import FakeQQ, buttons
from test_shell import NOW, PLUGIN, ShellFixture


def summary(**changes):
    return {"title": "When should the release ship?", "context": "The code is ready.",
            "why_user": "The release timing affects the user's schedule.",
            "recommendation_reason": "A review window reduces rework.", "impact": "Customers will see the release.",
            "options": [{"label": "Ship the tested release", "consequence": "Customers get it now."},
                        {"label": "Wait for user review", "consequence": "The release is delayed."}], **changes}


class RemoteRoutingTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.client, self.counter = FakeQQ(), 0
        (self.home / 'qq.json').write_text(json.dumps({'app_id': 'fake', 'app_secret': 'fake'}), encoding='utf-8')
        (self.home / 'notify.json').write_text(json.dumps(dict(owner_openid='owner', since=NOW, manual_away=False,
            current_leader_session_id=None, leader_numbers={})), encoding='utf-8')
        self.service = web.Service(self.home, PLUGIN, cwd=self.project, agents_provider=lambda: [],
            claude_root=self.claude, codex_root=self.codex, runs_file=self.runs, now=lambda: store.parse_iso(NOW),
            notification_client=self.client, lock_status=lambda: False, process_factory=self.process_factory,
            vscode_state_db=self.vscode_state_db)
        self.addCleanup(self.service.close)
        self.inbox = self.service.inbox

    def receive(self, text, ref=None):
        self.counter += 1
        self.inbox.receive('message', dict(message_id='input-%s' % self.counter, openid='owner', text=text,
                                          quoted_ref=ref, attachments=[]))

    def click(self, data):
        self.counter += 1
        self.inbox.receive('interaction', dict(interaction_id='click-%s' % self.counter, openid='owner',
                                              button_id='answer', data=data))

    def query(self, decision_id):
        self.receive('/decisions')
        row = self.conn.execute('SELECT message_id FROM notification_messages WHERE decision_id=? ORDER BY rowid DESC LIMIT 1',
                                (decision_id,)).fetchone()
        return next(message for message in self.client.sent if message['result']['id'] == row[0])

    def answer(self, decision_id):
        return self.conn.execute('SELECT answer FROM decisions WHERE id=?', (decision_id,)).fetchone()[0]

    def add_other_leader(self, project='Other', sid='other-lead', name='lead'):
        root = self.root / project
        root.mkdir(exist_ok=True)
        board.init_project(self.conn, project, root, now=NOW)
        board.join(self.conn, sid, project, name, root, root, 'main', role='leader', now=NOW)

    def sent_leader_message(self, kind='turn', sid='s3', project='demo'):
        self.counter += 1
        identity = 'outgoing-%s' % self.counter
        self.inbox.record_message(self.conn, identity, dict(workspace=project, leader_session_id=sid, decision=None), kind, NOW)
        self.inbox.deliver(self.conn, [identity], self.client.send_markdown, 'original leader message')
        return self.client.sent[-1]['result']['ref_idx']

    def replies(self):
        return self.conn.execute('SELECT * FROM notification_replies ORDER BY rowid').fetchall()

    def test_old_button_cannot_select_a_new_action_even_after_a_fresh_question_was_sent(self):
        number = board.ask(self.conn, 's1', 'Original technical question', ['Old action', 'Wait'], 'Old action')
        old = self.query(number)
        old_button = buttons(old)[0]['action']['data']
        decision_briefs.revise(self.conn, 's1', number, question='A different clarified choice',
                               recommendation='Ship the tested release', brief=summary())
        fresh = self.query(number)
        new_button = buttons(fresh)[0]['action']['data']
        self.assertNotEqual(old_button, new_button)
        self.click(old_button)
        self.assertIsNone(self.answer(number))
        self.assertIn('已更新', self.client.sent[-1]['text'])
        self.click(new_button)
        self.assertEqual(self.answer(number), 'Ship the tested release')

    def test_changed_brief_invalidates_a_button_even_when_action_labels_are_the_same(self):
        number = board.ask(self.conn, 's1', 'Question', recommendation='Wait for user review', brief=summary())
        old = self.query(number)
        decision_briefs.revise(self.conn, 's1', number, brief={'impact': 'A newly discovered customer impact'})
        self.click(buttons(old)[0]['action']['data'])
        self.assertIsNone(self.answer(number))
        self.assertIn('已更新', self.client.sent[-1]['text'])

    def test_legacy_numeric_buttons_are_accepted_only_while_the_original_choice_is_unchanged(self):
        unchanged = board.ask(self.conn, 's1', 'Legacy question', ['Continue', 'Stop'])
        self.query(unchanged)
        self.click('%s:0' % unchanged)
        self.assertEqual(self.answer(unchanged), 'Continue')
        revised = board.ask(self.conn, 's1', 'Question to clarify', ['Old action', 'Wait'])
        self.query(revised)
        decision_briefs.revise(self.conn, 's1', revised, recommendation='Wait for user review', brief=summary())
        self.query(revised)
        self.click('%s:0' % revised)
        self.assertIsNone(self.answer(revised))
        self.assertIn('已更新', self.client.sent[-1]['text'])

    def test_unsent_snapshot_token_is_rejected(self):
        number = board.ask(self.conn, 's1', 'Question', ['Continue'])
        self.query(number)
        self.click('%s:0:%s' % (number, '0' * 24))
        self.assertIsNone(self.answer(number))

    def test_final_write_rechecks_the_snapshot_if_the_cli_revised_after_initial_validation(self):
        number = board.ask(self.conn, 's1', 'Old question', ['Old action', 'Wait'])
        self.query(number)
        shown = self.conn.execute('SELECT * FROM notification_messages WHERE decision_id=?', (number,)).fetchone()
        old_snapshot = notifications.saved_snapshot(shown)
        old_decision = board.decisions(self.conn, 'demo')[0]
        decision_briefs.revise(self.conn, 's1', number, recommendation='Wait for user review', brief=summary())
        self.inbox.save_answer(self.conn, old_decision, 'Old action', 'simulated-interleaving', expected_snapshot=old_snapshot)
        self.assertIsNone(self.answer(number))
        self.assertIn('已更新', self.client.sent[-1]['text'])

    def test_answer_and_leader_notice_are_one_transaction(self):
        number = board.ask(self.conn, 's1', 'Question', ['Continue'])
        decision = board.decisions(self.conn, 'demo')[0]
        with mock.patch.object(board, '_queue_leader_notice', side_effect=RuntimeError('queue failed')), self.assertRaises(RuntimeError):
            self.inbox.save_answer(self.conn, decision, 'Continue', 'failed-notice')
        self.assertIsNone(self.answer(number))
        self.assertEqual(board.pending_notices(self.conn, 's3'), [])

    def test_answer_notice_uses_crew_address_for_either_codex_participant(self):
        for asker_runtime, leader_runtime in (('codex', 'claude'), ('claude', 'codex'), ('codex', 'codex')):
            with self.subTest(asker=asker_runtime, leader=leader_runtime):
                runtimes.set(self.conn, 's1', asker_runtime)
                runtimes.set(self.conn, 's3', leader_runtime)
                board.update_cc_name(self.conn, 's1', None)
                board.update_cc_name(self.conn, 's3', None)
                number = board.ask(self.conn, 's1', 'Mixed participant choice', ['Continue'])
                decision = next(item for item in board.decisions(self.conn, 'demo') if item['id'] == number)
                self.inbox.save_answer(self.conn, decision, 'Continue', 'mixed-%s' % number)
                notice = next(item['text'] for item in board.pending_notices(self.conn, 's3')
                              if item['text'].startswith('#%s\n' % number))
                self.assertIn('crew send one', notice)
                self.assertIn('Crew ID：s1', notice)
                self.assertNotIn('SendMessage', notice)
                self.assertEqual(self.answer(number), 'Continue')

    def test_codex_leaders_own_answer_does_not_tell_it_to_message_itself(self):
        runtimes.set(self.conn, 's3', 'codex')
        board.update_cc_name(self.conn, 's3', None)
        number = board.ask(self.conn, 's3', 'Leader own choice', ['Continue'])
        decision = next(item for item in board.decisions(self.conn, 'demo') if item['id'] == number)
        self.inbox.save_answer(self.conn, decision, 'Continue', 'own-answer')
        notice = board.pending_notices(self.conn, 's3')[0]['text']
        self.assertIn('请按用户答复继续', notice)
        self.assertNotIn('crew send lead', notice)
        self.assertNotIn('SendMessage', notice)

    def test_quoted_old_question_is_not_answered_after_its_meaning_changes(self):
        number = board.ask(self.conn, 's1', 'Old choice', ['Old action', 'Wait'])
        old = self.query(number)
        decision_briefs.revise(self.conn, 's1', number, recommendation='Wait for user review', brief=summary())
        self.receive('Agree with what I saw', old['result']['ref_idx'])
        self.assertIsNone(self.answer(number))
        self.assertIn('已更新', self.client.sent[-1]['text'])
        fresh = self.query(number)
        self.receive('Wait for user review', fresh['result']['ref_idx'])
        self.assertEqual(self.answer(number), 'Wait for user review')

    def test_quote_routes_turns_and_replies_to_the_original_leader_without_switching_selection(self):
        self.add_other_leader()
        self.receive('/leader')
        self.receive('/%s' % self.inbox.config['leader_numbers']['other-lead'])
        for kind in ('turn', 'reply'):
            with self.subTest(kind=kind):
                ref = self.sent_leader_message(kind)
                self.receive('Follow up on the original message', ref)
                self.assertEqual(self.replies()[-1]['session_id'], 's3')
                self.assertEqual(self.inbox.config['current_leader_session_id'], 'other-lead')
                self.assertIn('demo·lead', self.client.sent[-1]['text'])
        self.receive('A new ordinary message')
        self.assertEqual(self.replies()[-1]['session_id'], 'other-lead')

    def test_quote_to_an_archived_leader_never_falls_back_to_the_current_leader(self):
        self.add_other_leader()
        ref = self.sent_leader_message()
        self.receive('/leader')
        self.receive('/%s' % self.inbox.config['leader_numbers']['other-lead'])
        session_ops.archive(self.conn, 's3', now=NOW)
        self.receive('Reply to this one', ref)
        self.assertEqual(self.replies(), [])
        self.assertIn('归档', self.client.sent[-1]['text'])

    def test_quote_of_a_multi_leader_batch_requires_an_explicit_recipient(self):
        self.add_other_leader()
        for identity, project, sid in (('note-one', 'demo', 's3'), ('note-two', 'Other', 'other-lead')):
            self.inbox.record_message(self.conn, identity, dict(workspace=project, leader_session_id=sid, decision=None), 'report', NOW)
        self.inbox.deliver(self.conn, ['note-one', 'note-two'], self.client.send_text, 'Two project notes')
        ref = self.client.sent[-1]['result']['ref_idx']
        self.receive('/1')
        self.receive('Which one?', ref)
        self.assertEqual(self.replies(), [])
        self.assertIn('多个 leader', self.client.sent[-1]['text'])

    def test_project_and_named_leader_selection_is_explicit_case_preserving_and_persistent(self):
        self.add_other_leader()
        self.receive('/projects')
        self.assertIn('Other', self.client.sent[-1]['text'])
        numbers = dict(self.inbox.config['leader_numbers'])
        self.receive('/%s' % numbers['s3'])
        self.receive('/Project Other')
        self.assertEqual(self.inbox.config['current_project'], 'Other')
        self.assertIsNone(self.inbox.config['current_leader_session_id'])
        self.receive('Do not guess a leader')
        self.assertEqual(self.replies(), [])
        self.receive('/leader lead')
        self.assertEqual(self.inbox.config['current_leader_session_id'], 'other-lead')
        self.assertIn('已切换到 Other·lead', self.client.sent[-1]['text'])
        self.receive('/where')
        self.assertIn('Other·lead', self.client.sent[-1]['text'])
        restarted = notifications.Inbox(self.service, self.client, lock_status=lambda: False)
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.config['current_project'], 'Other')
        self.assertEqual(restarted.config['current_leader_session_id'], 'other-lead')
        self.assertEqual(restarted.config['leader_numbers'], numbers)
        self.receive('/%s' % numbers['s3'])
        self.assertEqual(self.inbox.config['current_project'], 'demo')
        self.assertEqual(self.inbox.config['leader_numbers'], numbers)

    def test_ambiguous_named_leader_does_not_pick_the_first_project(self):
        self.add_other_leader()
        self.receive('/leader lead')
        self.assertIsNone(self.inbox.config['current_leader_session_id'])
        self.assertIn('多个项目', self.client.sent[-1]['text'])

    def test_structured_qq_question_shows_the_reason_and_consequences(self):
        number = board.ask(self.conn, 's1', 'A long technical original', recommendation='Wait for user review', brief=summary())
        message = self.query(number)
        for field in ('title', 'why_user', 'recommendation_reason', 'impact'):
            self.assertIn(summary()[field], message['text'])
        for option in summary()['options']:
            self.assertIn(option['consequence'], message['text'])
        self.assertNotIn('A long technical original', message['text'])


if __name__ == '__main__':
    unittest.main()
