"""Context isolation, correction, actual acceptance and protected budgets."""
import json
import unittest

import tests
from crew import board, goal_context as context, goals
from tests.test_goals import GoalFixture


class GoalContextTests(GoalFixture):
    def execution(self, goal):
        return goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='codex')

    def item(self, goal, text='Short phrases fail', **options):
        return context.put(self.conn, goal['id'], 'fact', text, {'type': 'measurement', 'id': 'sample-1', 'version': 1}, **options)

    def test_same_identity_two_goals_and_project_rules_are_scoped(self):
        a, b = self.goal(), self.goal()
        self.item(a, 'Only speaker goal contains this marker')
        ea, eb = self.execution(a), self.execution(b)
        context.put(self.conn, None, 'rule', 'Approved project rule', {'type': 'user', 'id': 'confirmed'},
                    scope='project', project='demo', confirmed=True)
        loaded = context.build(self.conn, ea['id'])
        other = context.build(self.conn, eb['id'])
        self.assertIn('Only speaker goal', loaded['text'])
        self.assertNotIn('Only speaker goal', other['text'])
        self.assertIn('Approved project rule', other['text'])
        with self.assertRaises(board.BoardError):
            context.inspect(self.conn, a['id'], eb['id'])

    def test_unconfirmed_guesses_cannot_be_promoted_to_long_term_rules(self):
        for options in ({'confirmed': False}, {'confirmed': True, 'actor': 'chief-id'}):
            with self.assertRaises(board.BoardError):
                context.put(self.conn, None, 'rule', 'Always do this', {'type': 'guess', 'id': 'x'},
                            scope='project', project='demo', **options)
        goal = self.goal()
        with self.assertRaises(board.BoardError):
            context.put(self.conn, goal['id'], 'fact', 'Guess', {'type': 'guess', 'id': 'x'}, scope='project', confirmed=True)

    def test_preparing_is_pending_only_native_acceptance_enables_delta_deduplication(self):
        goal, item = self.goal(), None
        item = self.item(goal)
        execution = self.execution(goal)
        first = context.build(self.conn, execution['id'], base='Identity and required routing only')
        self.assertEqual(context.inspect(self.conn, goal['id'], execution['id'])['items'][0]['application'], 'pending')
        second = context.build(self.conn, execution['id'], base='Identity and required routing only')
        self.assertEqual(first['text'], second['text'])
        context.mark_applied(self.conn, first['manifest']['id'], execution_id=execution['id'])
        third = context.build(self.conn, execution['id'], base='Identity and required routing only')
        self.assertEqual(third['text'], '')
        self.assertTrue(all(entry['inherited'] for entry in third['manifest']['entries']))
        self.assertEqual(context.inspect(self.conn, goal['id'], execution['id'])['items'][0]['application'], 'applied')
        self.assertFalse(context.manifest(self.conn, third['manifest']['id'])['applied'])

    def test_correction_recursively_expires_summaries_and_retracts_native_old_fact(self):
        goal = self.goal()
        fact = self.item(goal, 'Old wrong result')
        summary = context.put(self.conn, goal['id'], 'summary', 'Old derived summary', {'type': 'handoff', 'id': 'h1'},
                              depends=[{'type': 'item', 'id': fact['id'], 'version': 1}])
        derived = context.put(self.conn, goal['id'], 'summary', 'Old second summary', {'type': 'handoff', 'id': 'h2'},
                              depends=[{'type': 'item', 'id': summary['id'], 'version': 1}])
        execution = self.execution(goal)
        first = context.build(self.conn, execution['id'])
        context.mark_applied(self.conn, first['manifest']['id'])
        changed = context.correct(self.conn, fact['id'], 'Corrected measured result')
        self.assertIn(summary['id'], changed['invalidated'])
        self.assertEqual(context.get(self.conn, derived['id'])['state'], 'obsolete')
        next_input = context.build(self.conn, execution['id'])
        self.assertIn('Corrected measured result', next_input['text'])
        self.assertNotIn('Old derived summary', next_input['text'])
        self.assertIn('旧依据已失效', next_input['text'])
        self.assertEqual(context.get(self.conn, fact['id'], 1)['text'], 'Old wrong result')
        self.assertEqual(next(item for item in context.inspect(self.conn, goal['id'], execution['id'])['items'] if item['id'] == fact['id'])['application'], 'pending')

    def test_old_source_cannot_be_reintroduced_after_correction_or_across_goals(self):
        goal, other = self.goal(), self.goal()
        fact = self.item(goal)
        context.correct(self.conn, fact['id'], 'New result')
        for target, version in ((goal, 1), (other, 2)):
            with self.assertRaises(board.BoardError):
                context.put(self.conn, target['id'], 'summary', 'Invalid source', {'type': 'handoff', 'id': 'stale'},
                            depends=[{'type': 'item', 'id': fact['id'], 'version': version}])

    def test_requirement_change_preserves_constraints_and_invalidates_old_summary(self):
        goal = self.goal(constraints=['Do not PR; wait for the user'])
        summary = context.put(self.conn, goal['id'], 'summary', 'Prior requirement summary', {'type': 'handoff', 'id': 'h'},
                              depends=[{'type': 'requirement', 'id': goal['id'], 'version': 1}])
        execution = self.execution(goal)
        context.mark_applied(self.conn, context.build(self.conn, execution['id'])['manifest']['id'])
        goals.amend(self.conn, goal['id'], 'Do not delete recordings')
        loaded = context.build(self.conn, execution['id'])
        self.assertIn('Do not PR; wait', loaded['text'])
        self.assertIn('Do not delete recordings', loaded['text'])
        self.assertNotIn('Prior requirement summary', loaded['text'])
        self.assertEqual(context.get(self.conn, summary['id'])['state'], 'obsolete')
        self.assertFalse(goals.dispatch_check(self.conn, goal['id'], execution_id=execution['id'])['allowed'])
        context.mark_applied(self.conn, loaded['manifest']['id'])
        self.assertTrue(goals.dispatch_check(self.conn, goal['id'], execution_id=execution['id'])['allowed'])

    def test_protected_requirements_and_decisions_fail_loudly_instead_of_truncating(self):
        goal = self.goal(constraints=['Never publish the private recording'])
        execution = self.execution(goal)
        with self.assertRaisesRegex(board.BoardError, '保护材料超限'):
            context.build(self.conn, execution['id'], limits={'work': 20})
        context.put(self.conn, goal['id'], 'decision', 'Confirmed waiting decision ' * 50,
                    {'type': 'user', 'id': 'approval'}, confirmed=True)
        with self.assertRaises(board.BoardError):
            context.build(self.conn, execution['id'], limits={'work': 300})
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM goal_context_manifests').fetchone()[0], 0)

    def test_retrieval_budget_is_explicit_truncation_and_native_history_is_not_claimed_zero(self):
        goal = self.goal()
        execution = self.execution(goal)
        output = context.build(self.conn, execution['id'], retrieval=[{'text': 'x' * 500, 'source': {'type': 'file', 'id': 'log.txt'}, 'reason': 'Relevant failure excerpt'}], limits={'retrieval': 40})
        entry = next(item for item in output['manifest']['entries'] if item['section'] == 'retrieval')
        self.assertTrue(entry['truncated'])
        self.assertLessEqual(entry['chars'], 40)
        self.assertEqual(output['manifest']['counts']['retrieval'], 40)
        self.assertEqual(entry['source']['id'], 'log.txt')
        self.assertFalse(output['manifest']['native_history']['known'])
        self.assertEqual(output['manifest']['measurement'], 'characters_not_tokens')
        rotated = context.build(self.conn, execution['id'], native_history={'known': True, 'tokens': 5000, 'estimated': True}, limits={'native_history_tokens': 4000})
        self.assertTrue(rotated['manifest']['rotation_required'])

    def test_late_acceptance_does_not_roll_back_a_newer_applied_version(self):
        goal = self.goal()
        execution = self.execution(goal)
        old = context.build(self.conn, execution['id'])
        goals.amend(self.conn, goal['id'], 'New direction')
        new = context.build(self.conn, execution['id'])
        context.mark_applied(self.conn, new['manifest']['id'])
        context.mark_applied(self.conn, old['manifest']['id'])
        self.assertEqual(context.inspect(self.conn, goal['id'], execution['id'])['requirements_application'], 'applied')
        self.assertTrue(context.manifest(self.conn, old['manifest']['id'])['applied'])

    def test_manifest_receipt_is_bound_to_execution_and_ordinary_lookup_creates_nothing(self):
        a, b = self.goal(), self.goal()
        ea, eb = self.execution(a), self.execution(b)
        manifest = context.build(self.conn, ea['id'])
        with self.assertRaises(board.BoardError):
            context.mark_applied(self.conn, manifest['manifest']['id'], execution_id=eb['id'])
        self.assertFalse(context.manifest(self.conn, manifest['manifest']['id'])['applied'])
        self.assertEqual(len(goals.list(self.conn, 'demo')), 2)
        json.dumps(context.inspect(self.conn, a['id'], ea['id']))

    def test_correction_cannot_form_a_dependency_cycle_or_float_to_an_old_self(self):
        goal = self.goal()
        first = self.item(goal)
        second = context.put(self.conn, goal['id'], 'summary', 'Derived from first', {'type': 'report', 'id': 'derived'},
                             depends=[{'type': 'item', 'id': first['id']}])
        self.assertEqual(second['depends'][0]['version'], 1)
        with self.assertRaises(board.BoardError):
            context.correct(self.conn, first['id'], 'Would be a cycle', depends=[{'type': 'item', 'id': second['id'], 'version': 1}])
        with self.assertRaises(board.BoardError):
            context.correct(self.conn, first['id'], 'Would cite self', source={'type': 'item', 'id': first['id'], 'version': 1})
        self.assertEqual(context.get(self.conn, first['id'])['version'], 1)

    def test_bad_limit_names_do_not_silently_disable_a_budget(self):
        goal = self.goal()
        execution = self.execution(goal)
        for limits in ({'work_chars': 10}, {'work': True}, {'native_history_tokens': -1}):
            with self.assertRaises(board.BoardError):
                context.build(self.conn, execution['id'], limits=limits)

    def test_foreign_workspace_context_and_its_sources_cannot_leak_into_a_goal(self):
        goal = self.goal()
        root = self.root / 'foreign'
        root.mkdir()
        board.init_project(self.conn, 'foreign', root)
        board.join(self.conn, 'foreign-chief', 'foreign', 'foreign', root, root, 'main', role='leader')
        other = goals.create(self.conn, 'foreign', 'foreign-chief', 'Foreign goal', 'Foreign request', ['Check'])
        item = context.put(self.conn, other['id'], 'fact', 'PRIVATE FOREIGN MATERIAL', {'type': 'file', 'id': 'private'})
        context.put(self.conn, None, 'rule', 'PRIVATE FOREIGN RULE', {'type': 'user', 'id': 'rule'},
                    scope='project', project='foreign', confirmed=True)
        output = context.build(self.conn, self.execution(goal)['id'])
        self.assertNotIn('PRIVATE FOREIGN', output['text'])
        with self.assertRaises(board.BoardError):
            context.put(self.conn, goal['id'], 'summary', 'Cross-project', {'type': 'item', 'id': item['id'], 'version': 1})

    def test_runtime_switch_reloads_requirements_and_old_carrier_receipt_cannot_poison_new_ledger(self):
        goal = self.goal()
        execution = goals.create_execution(self.conn, goal['id'], 'chief-id', runtime='claude')
        first = context.build(self.conn, execution['id'])
        late = context.build(self.conn, execution['id'])
        context.mark_applied(self.conn, first['manifest']['id'])
        version = goals.get(self.conn, goal['id'])['version']
        goals.configure_execution(self.conn, execution['id'], 'claude')
        self.assertEqual(goals.get(self.conn, goal['id'])['version'], version)
        goals.configure_execution(self.conn, execution['id'], 'codex')
        self.assertGreater(goals.get(self.conn, goal['id'])['version'], version)
        loaded = context.build(self.conn, execution['id'])
        self.assertIn(goal['original_request'], loaded['text'])
        self.assertEqual(loaded['manifest']['runtime'], 'codex')
        context.mark_applied(self.conn, late['manifest']['id'])
        self.assertEqual(context.inspect(self.conn, goal['id'], execution['id'])['requirements_application'], 'pending')
        context.mark_applied(self.conn, loaded['manifest']['id'])
        self.assertEqual(context.build(self.conn, execution['id'])['text'], '')


if __name__ == '__main__':
    unittest.main()
