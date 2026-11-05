"""Delivery, human acceptance and leader cleanup against isolated real SQLite."""
import json
import subprocess
from unittest import mock

import tests
from crew import board, goal_actions, goal_api, goals, runtimes, session_ops, todo
from tests.test_goals import GoalFixture


class LeaderClosureTests(GoalFixture):
    def ready(self, actor='chief-id', **fields):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        report = goals.save_report(self.conn, goal['id'], {**self.report('awaiting_acceptance'), **fields})
        goals.request_finish(self.conn, goal['id'], report['id'], actor=actor)
        result = goals.finalize_finish(self.conn, goal['id'])
        self.assertTrue(result['finalized'])
        return result['goal'], report

    def test_no_finish_or_transition_completes_a_task_without_the_users_acceptance(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        report = goals.save_report(self.conn, goal['id'], self.report())
        # The CLI falls back to the user identity when no session is set, so even "user" cannot finish as completed.
        for actor in ('chief-id', 'member-id', board.HUMAN):
            with self.subTest(actor=actor), self.assertRaisesRegex(board.BoardError, 'outcome 用 awaiting_acceptance'):
                goals.request_finish(self.conn, goal['id'], report['id'], actor=actor)
        for actor in ('chief-id', board.HUMAN):
            with self.subTest(actor=actor), self.assertRaisesRegex(board.Conflict, '用户本人'):
                goals.transition(self.conn, goal['id'], 'completed', actor=actor)
        self.assertIsNone(goals.get_finish(self.conn, goal['id']))
        waiting, _ = self.ready()
        for actor in ('chief-id', board.HUMAN):
            with self.subTest(actor=actor), self.assertRaisesRegex(board.Conflict, '用户本人'):
                goals.transition(self.conn, waiting['id'], 'completed', actor=actor)
        with self.assertRaisesRegex(board.BoardError, '用户本人'):
            goals.review_acceptance(self.conn, waiting['id'], True, actor='chief-id', expected_version=waiting['version'])
        # AI goal tools expose no acceptance action at all.
        with self.assertRaisesRegex(board.BoardError, 'action'):
            goal_actions.dispatch(self.home, 'chief-id', None, 'accept', {'goal_id': waiting['id']})
        self.assertEqual(goals.get(self.conn, waiting['id'])['status'], 'awaiting_acceptance')
        done = goals.review_acceptance(self.conn, waiting['id'], True, expected_version=waiting['version'])
        self.assertEqual(done['status'], 'completed')

    def test_user_test_and_todos_require_nonempty_text_objects(self):
        goal = self.goal()
        data = self.report('awaiting_acceptance')
        missing = dict(data)
        del missing['user_test']
        with self.assertRaisesRegex(board.BoardError, 'user_test'):
            goals.save_report(self.conn, goal['id'], missing)
        for field, values in (('user_test', ([], None, ['step'], [{'step': 'a', 'expected': ' '}], [{'expected': 'b'}])),
                              ('user_todos', (None, ['todo'], [{'title': '', 'detail': 'a'}], [{'title': 'a'}]))):
            for value in values:
                with self.subTest(field=field, value=value), self.assertRaises(board.BoardError):
                    goals.save_report(self.conn, goal['id'], {**data, field: value})
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_reports').fetchone()[0], 0)
        for outcome in ('completed', 'failed', 'cancelled', 'progress'):
            goals.save_report(self.conn, goal['id'], {**missing, 'outcome': outcome, 'acceptance': self.report()['acceptance']})

    def test_user_test_is_saved_and_escaped_in_standard_and_authored_reports(self):
        goal = self.goal()
        steps = [{'step': 'Open <button>', 'expected': 'See "Ready" & continue'}, {'step': 'Click save', 'expected': 'Saved'}]
        for document in ('', '<h1>My report</h1>'):
            with self.subTest(document=document):
                report = goals.save_report(self.conn, goal['id'], {**self.report('awaiting_acceptance'), 'user_test': steps, 'document_html': document})
                self.assertEqual(goals.get_report(self.conn, report['id'])['data']['user_test'], steps)
                self.assertIn('<h2>请你实测</h2><ol>', report['html'])
                self.assertIn('Open &lt;button&gt;', report['html'])
                self.assertIn('预期：See &quot;Ready&quot; &amp; continue', report['html'])
                self.assertLess(report['html'].index('Open &lt;button&gt;'), report['html'].index('Click save'))

    def test_finalize_creates_unlinked_leader_todos_once_for_ai_and_human(self):
        for actor in ('chief-id', board.HUMAN):
            with self.subTest(actor=actor):
                goal, report = self.ready(actor, user_todos=[{'title': 'Connect device', 'detail': 'Plug in cable'},
                                                          {'title': 'Sign in', 'detail': 'Use your account'}])
                first = todo.list(self.conn, 'demo', all=True)
                goals.request_finish(self.conn, goal['id'], report['id'], actor=actor)
                self.assertTrue(goals.finalize_finish(self.conn, goal['id'])['already'])
                self.assertEqual(todo.list(self.conn, 'demo', all=True), first)
                items = [item for item in first if item['operation_key'].startswith('report:' + report['id'])]
                self.assertEqual([item['title'] for item in items], ['Connect device', 'Sign in'])
                for index, item in enumerate(items, 1):
                    self.assertEqual(item['session_id'], 'chief-id')
                    self.assertEqual(item['operation_key'], 'report:%s:%s' % (report['id'], index))
                    self.assertEqual(item['goal_ids'], [])
                    self.assertEqual(item['goal_links'], [])
                self.assertEqual(items[0]['steps'], '来自任务「%s」：Plug in cable' % goal['title'])
                # Re-enter the creation path after a partial external retry, using its stable key.
                duplicate = todo.add_native(self.conn, 'chief-id', items[0]['title'], items[0]['steps'], items[0]['verification'],
                                            operation_key=items[0]['operation_key'])
                self.assertEqual(duplicate['id'], items[0]['id'])
                todo.resolve(self.conn, 'demo', items[0]['id'], 'done')
                self.assertIn('人工待办', board.pending_notices(self.conn, 'chief-id')[-1]['text'])
                self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'awaiting_acceptance')

    def test_todo_failure_rolls_back_finalize_and_retries_without_duplicates(self):
        goal = self.goal()
        goals.transition(self.conn, goal['id'], 'running')
        report = goals.save_report(self.conn, goal['id'], {**self.report('awaiting_acceptance'),
            'user_todos': [{'title': 'Connect device', 'detail': 'Cable'}, {'title': 'Sign in', 'detail': 'Account'}]})
        goals.request_finish(self.conn, goal['id'], report['id'], actor='chief-id')
        original = todo.add_native
        def fail_second(*args, **kwargs):
            if args[2] == 'Sign in':
                raise board.BoardError('injected todo failure')
            return original(*args, **kwargs)
        with mock.patch.object(todo, 'add_native', side_effect=fail_second), self.assertRaisesRegex(board.BoardError, 'injected'):
            goals.finalize_finish(self.conn, goal['id'])
        self.assertEqual(todo.list(self.conn, 'demo'), [])
        self.assertEqual(goals.get_finish(self.conn, goal['id'])['status'], 'pending')
        self.assertEqual(goals.get(self.conn, goal['id'])['status'], 'running')
        self.assertTrue(goals.finalize_finish(self.conn, goal['id'])['finalized'])
        self.assertEqual(len(todo.list(self.conn, 'demo')), 2)

    def test_card_brief_uses_current_round_requirement_and_report_version(self):
        goal, report = self.ready()
        newer = goals.save_report(self.conn, goal['id'], {**self.report('awaiting_acceptance'), 'summary': 'New summary'}, now='2020-01-01T00:00:00Z')
        card = goal_api.card(self.conn, goals.get(self.conn, goal['id']))
        self.assertEqual(card['acceptance_brief'], {'report_id': newer['id'], 'summary': 'New summary',
            'user_test': newer['data']['user_test'], 'created_at': newer['created_at'], 'supplements_after_report': []})
        goals.review_acceptance(self.conn, goal['id'], False, note='Please fix.', expected_version=goal['version'])
        self.assertIsNone(goal_api.card(self.conn, goals.get(self.conn, goal['id']))['acceptance_brief'])
        goals.transition(self.conn, goal['id'], 'running')
        goals.transition(self.conn, goal['id'], 'awaiting_acceptance')
        # Version is a record now: the round's latest report stays current and
        # the rejection note is listed as a supplement after it.
        brief = goal_api.card(self.conn, goals.get(self.conn, goal['id']))['acceptance_brief']
        self.assertEqual(brief['report_id'], newer['id'])
        self.assertEqual([item['text'] for item in brief['supplements_after_report']], ['Please fix.'])
        third = goals.save_report(self.conn, goal['id'], self.report('awaiting_acceptance'))
        self.assertEqual(goal_api.card(self.conn, goals.get(self.conn, goal['id']))['acceptance_brief']['report_id'], third['id'])
        goals.reopen(self.conn, goal['id'], 'A new round')
        goals.transition(self.conn, goal['id'], 'running')
        goals.transition(self.conn, goal['id'], 'awaiting_acceptance')
        self.assertIsNone(goal_api.card(self.conn, goals.get(self.conn, goal['id']))['acceptance_brief'])

    def test_card_does_not_invent_steps_or_rewrite_a_preexisting_report(self):
        goal, report = self.ready()
        historical = dict(report['data'])
        del historical['user_test']
        encoded = json.dumps(historical)
        self.conn.execute('UPDATE goal_reports SET data_json=? WHERE id=?', (encoded, report['id']))
        self.conn.commit()
        brief = goal_api.card(self.conn, goals.get(self.conn, goal['id']))['acceptance_brief']
        self.assertIsNone(brief['user_test'])
        self.assertEqual(brief['report_id'], report['id'])
        self.assertEqual(self.conn.execute('SELECT data_json FROM goal_reports WHERE id=?', (report['id'],)).fetchone()[0], encoded)

    def test_acceptance_notifies_once_with_only_eligible_members(self):
        for sid, name in (('free', 'Free worker'), ('busy', 'Other task worker'), ('admin', 'Git admin')):
            board.join(self.conn, sid, 'demo', name, self.root, self.root, 'main', group_name='chief')
        goal, report = self.ready()
        # Assignments are made before review; ready already prevents new dispatch.
        for sid in ('free', 'busy', 'admin'):
            self.conn.execute('INSERT INTO goal_assignments VALUES(?,?,?,?,?,?)', (goal['id'], sid, 'Original task', 'assigned', goal['created_at'], goal['created_at']))
        self.conn.commit()
        other = self.goal()
        goals.assign(self.conn, other['id'], 'busy', 'Another unfinished task')
        for args in (('init', '-b', 'main'), ('-c', 'user.name=Crew Test', '-c', 'user.email=test@example.invalid',
                      '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-m', 'Fixture')):
            subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True)
        board.enable_git(self.conn, 'demo', admin_session_id='admin')
        result = goals.review_acceptance(self.conn, goal['id'], True, expected_version=goal['version'])
        self.assertEqual(result['status'], 'completed')
        notices = board.pending_notices(self.conn, 'chief-id')
        self.assertEqual(len(notices), 1)
        self.assertIn('验收通过', notices[0]['text'])
        self.assertIn('Free worker', notices[0]['text'])
        self.assertNotIn('Other task worker', notices[0]['text'])
        self.assertNotIn('Git admin', notices[0]['text'])
        self.assertIn('crew archive', notices[0]['text'])
        with self.assertRaises(board.Conflict):
            goals.review_acceptance(self.conn, goal['id'], True, expected_version=goal['version'])
        self.assertEqual(len(board.pending_notices(self.conn, 'chief-id')), 1)

    def test_reject_continues_same_task_round_and_notifies_once(self):
        goal, report = self.ready()
        result = goals.review_acceptance(self.conn, goal['id'], False, note='Keep <this> exact opinion.', expected_version=goal['version'])
        self.assertEqual((result['id'], result['current_round'], result['status']), (goal['id'], 1, 'queued'))
        self.assertEqual(result['requirement_version'], goal['requirement_version'] + 1)
        self.assertIn('Keep <this> exact opinion.', result['requirements']['request'])
        self.assertEqual(goals.report_review(self.conn, goal['id'], report['id'])['note'], 'Keep <this> exact opinion.')
        self.assertEqual(goals.get_report(self.conn, report['id'])['html'], report['html'])
        self.assertIn('Keep <this> exact opinion.', board.pending_notices(self.conn, 'chief-id')[0]['text'])
        with self.assertRaises(board.Conflict):
            goals.review_acceptance(self.conn, goal['id'], False, note='Keep <this> exact opinion.', expected_version=goal['version'])
        self.assertEqual(len(board.pending_notices(self.conn, 'chief-id')), 1)

    def test_archive_only_current_group_leader_and_idle_members(self):
        board.join(self.conn, 'foreign-chief', 'demo', 'foreign', self.root, self.root, 'main', role='leader')
        for target, caller in (('member-id', 'member-id'), ('chief-id', 'member-id'), ('member-id', 'foreign-chief'), ('chief-id', 'chief-id')):
            with self.subTest(target=target, caller=caller), self.assertRaises(board.BoardError):
                session_ops.archive(self.conn, target, caller_session_id=caller)
        goal = self.goal()
        goals.assign(self.conn, goal['id'], 'member-id', 'Work')
        execution = goals.create_execution(self.conn, goal['id'], 'member-id', runtime='codex')
        goals.set_execution_status(self.conn, execution['id'], 'running')
        with self.assertRaisesRegex(board.Conflict, '先等执行结束'):
            session_ops.archive(self.conn, 'member-id', caller_session_id='chief-id')
        goals.set_execution_status(self.conn, execution['id'], 'idle')
        self.assertTrue(session_ops.archive(self.conn, 'member-id', caller_session_id='chief-id')['ok'])

    def test_close_group_rejects_member_and_foreign_leader_and_records_actor(self):
        board.join(self.conn, 'foreign-chief', 'demo', 'foreign', self.root, self.root, 'main', role='leader')
        for caller in ('member-id', 'foreign-chief'):
            with self.subTest(caller=caller), self.assertRaises(board.BoardError):
                session_ops.close_group(self.conn, 'demo', 'chief', actor=caller)
        session_ops.close_group(self.conn, 'demo', 'chief', actor='chief-id')
        event = self.conn.execute("SELECT session_id FROM events WHERE kind='leader:close'").fetchone()
        self.assertEqual(event['session_id'], 'chief-id')
