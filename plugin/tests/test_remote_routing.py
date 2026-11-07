"""QQ routing uses sent snapshots and stable Crew ids; no real messages or models."""
import tests
import json
import unittest
from unittest import mock

from crew import board, decision_briefs, notifications, personal, runtimes, session_ops, store, web
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
        (self.home / 'notify.json').write_text(json.dumps(dict(owner_openid='owner', since=NOW, manual_away=False)), encoding='utf-8')
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

    def test_quoted_old_question_is_context_for_assistant_without_direct_answer(self):
        sid = personal.ensure_assistant(self.conn, self.home)['session_id']
        number = board.ask(self.conn, 's1', 'Old choice', ['Old action', 'Wait'])
        old = self.query(number)
        decision_briefs.revise(self.conn, 's1', number, recommendation='Wait for user review', brief=summary())
        self.receive('Agree with what I saw', old['result']['ref_idx'])
        self.assertIsNone(self.answer(number))
        self.assertEqual(self.replies()[0]['session_id'], sid)
        self.assertIn('decision_id=' + str(number), json.loads(self.replies()[0]['text'])['text'])



    def test_quote_of_multi_leader_batch_preserves_all_sources_for_assistant(self):
        sid = personal.ensure_assistant(self.conn, self.home)['session_id']
        self.add_other_leader()
        for identity, project, leader in (('note-one', 'demo', 's3'), ('note-two', 'Other', 'other-lead')):
            self.inbox.record_message(self.conn, identity, dict(workspace=project, leader_session_id=leader,
                decision=None, item={'goal_id': identity}), 'goal_report', NOW)
        self.inbox.deliver(self.conn, ['note-one', 'note-two'], self.client.send_text, 'Two project notes')
        self.receive('Which one?', self.client.sent[-1]['result']['ref_idx'])
        self.assertEqual(self.replies()[0]['session_id'], sid)
        text = json.loads(self.replies()[0]['text'])['text']
        for part in ('demo', 'Other', 'session_id=s3', 'session_id=other-lead', 'goal_id=note-one', 'goal_id=note-two'):
            self.assertIn(part, text)



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
