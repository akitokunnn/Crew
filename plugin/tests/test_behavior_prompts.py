"""Prompt scope/version history and delivered-content evidence stay truthful."""
import tests
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from crew import behavior_prompts as prompts
from crew import board, goals, settings, store


class BehaviorPromptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='crew-behavior-prompts-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.conn = store.connect(self.root / 'home')
        self.addCleanup(self.conn.close)
        for name in ('one', 'two'):
            folder = self.root / name
            folder.mkdir()
            board.init_project(self.conn, name, folder)
            board.register_session(self.conn, 'leader-' + name, name, str(folder), None, None, name='leader', role='leader')
        self.goal = goals.create(self.conn, 'one', 'leader-one', 'Synthetic task', 'Synthetic work', ['Verify fixture'])
        self.execution = goals.create_execution(self.conn, self.goal['id'], 'leader-one', runtime='codex')

    def test_six_defaults_are_visible_with_exact_content_without_database_writes_or_native_tool(self):
        previous_changes = self.conn.total_changes
        with mock.patch('subprocess.Popen', side_effect=AssertionError('No real AI')):
            manifest = prompts.resolve(self.conn, session_id='leader-one')
        self.assertEqual(set(item['key'] for item in manifest['prompts']), {'common', 'leader', 'member', 'assistant', 'report', 'context'})
        self.assertTrue(all(item['content'] == prompts.DEFAULTS[item['key']] for item in manifest['prompts']))
        self.assertTrue(all(item['source']['scope'] == 'default' for item in manifest['prompts']))
        self.assertEqual(manifest['native_private_instructions'], 'not_controlled')
        self.assertEqual(self.conn.total_changes, previous_changes)

    def test_each_kind_can_be_edited_versioned_and_restored(self):
        for key in prompts.KEYS:
            with self.subTest(key=key):
                value = prompts.save(self.conn, key, 'User instruction ' + key, expected_version=0)
                self.assertEqual(value['version'], 1)
                self.assertEqual(value['effective']['content'], 'User instruction ' + key)
                self.assertEqual(value['effective']['source']['scope'], 'global')
                restored = prompts.reset(self.conn, key, expected_version=1)
                self.assertFalse(restored['overridden'])
                self.assertEqual(restored['effective']['content'], prompts.DEFAULTS[key])
                history = prompts.history(self.conn, key)['history']
                self.assertEqual([entry['action'] for entry in history], ['reset', 'save'])
                self.assertEqual(history[1]['content'], 'User instruction ' + key)
                self.assertEqual(history[0]['effective_content'], prompts.DEFAULTS[key])

    def test_session_and_workspace_overrides_are_inherited_and_reset_without_affecting_others(self):
        prompts.save(self.conn, 'common', 'Global behavior', expected_version=0)
        prompts.save(self.conn, 'common', 'Workspace behavior', scope='project', identity='one', expected_version=0)
        prompts.save(self.conn, 'common', 'AI behavior', scope='session', identity='leader-one', expected_version=0)
        current = prompts.resolve(self.conn, session_id='leader-one', keys=('common',))['prompts'][0]
        self.assertEqual(current['content'], 'AI behavior')
        self.assertEqual(current['source'], {'scope': 'session', 'identity': 'leader-one'})
        reset = prompts.reset(self.conn, 'common', scope='session', identity='leader-one', expected_version=1)
        self.assertEqual(reset['effective']['content'], 'Workspace behavior')
        other = prompts.resolve(self.conn, session_id='leader-two', keys=('common',))['prompts'][0]
        self.assertEqual(other['content'], 'Global behavior')

    def test_stale_ai_edit_rejected_preserving_user_content_and_history(self):
        prompts.save(self.conn, 'report', 'User report rules', expected_version=0)
        with self.assertRaises(board.Conflict):
            prompts.save(self.conn, 'report', 'Stale AI rules', expected_version=0, actor='leader-one')
        self.assertEqual(prompts.get(self.conn, 'report')['effective']['content'], 'User report rules')
        self.assertEqual(len(prompts.history(self.conn, 'report')['history']), 1)

    def test_recorded_sent_manifest_survives_later_edits_and_does_not_claim_native_loading(self):
        prompts.save(self.conn, 'leader', 'Instruction at send', expected_version=0)
        manifest = prompts.resolve(self.conn, session_id='leader-one', keys=('common', 'leader', 'report', 'context'))
        sent = prompts.record_applied(self.conn, self.execution['id'], manifest, status='sent', identity='turn-one')
        self.assertIn('Instruction at send', prompts.render(sent['manifest']))
        prompts.save(self.conn, 'leader', 'Instruction after send', expected_version=1)
        inspected = prompts.inspect_applied(self.conn, self.execution['id'])
        self.assertEqual(inspected['loaded'], 'not_confirmed')
        self.assertEqual(inspected['applications'][0]['manifest'], manifest)
        self.assertEqual(inspected['applications'][0]['status'], 'sent')
        self.assertEqual(prompts.record_applied(self.conn, self.execution['id'], manifest, status='sent', identity='turn-one'), sent)
        with self.assertRaises(board.Conflict):
            prompts.record_applied(self.conn, self.execution['id'], prompts.resolve(self.conn, session_id='leader-one'),
                                   status='sent', identity='turn-one')

    def test_confirmed_loading_requires_evidence_and_rejects_cross_task_identity_or_corrupt_manifest(self):
        manifest = prompts.resolve(self.conn, session_id='leader-one', keys=('common',))
        with self.assertRaisesRegex(settings.SettingsError, '真实载体证据'):
            prompts.record_applied(self.conn, self.execution['id'], manifest, status='confirmed')
        value = prompts.record_applied(self.conn, self.execution['id'], manifest, status='confirmed', evidence='Fixture adapter accepted exact prompt hash')
        self.assertEqual(value['status'], 'confirmed')
        self.assertEqual(prompts.inspect_applied(self.conn, self.execution['id'])['loaded'], 'confirmed')
        with self.assertRaisesRegex(settings.SettingsError, '不属于指定执行'):
            prompts.record_applied(self.conn, self.execution['id'], prompts.resolve(self.conn, session_id='leader-two'))
        manifest['prompts'][0]['content'] = 'Corrupt text'
        with self.assertRaisesRegex(settings.SettingsError, '摘要无效'):
            prompts.record_applied(self.conn, self.execution['id'], manifest)

    def test_scope_and_pagination_validation(self):
        for version in range(4):
            prompts.save(self.conn, 'context', 'Context ' + str(version), expected_version=version)
        first = prompts.history(self.conn, 'context', limit=2)
        second = prompts.history(self.conn, 'context', limit=2, before=first['next_before'])
        self.assertEqual([entry['version'] for entry in first['history'] + second['history']], [4, 3, 2, 1])
        self.assertIsNone(second['next_before'])
        for kwargs in ({'limit': 0}, {'limit': 201}, {'before': True}):
            with self.assertRaises(settings.SettingsError):
                prompts.history(self.conn, 'context', **kwargs)
        with self.assertRaises(settings.SettingsError):
            prompts.resolve(self.conn, project='two', session_id='leader-one')
        with self.assertRaises(settings.SettingsError):
            prompts.resolve(self.conn, keys=('common', 'common'))
        for invalid in (1, 'common', [{}]):
            with self.assertRaises(settings.SettingsError):
                prompts.resolve(self.conn, keys=invalid)
        with self.assertRaises(settings.SettingsError):
            prompts.save(self.conn, 'unknown', 'text', expected_version=0)

    def test_rollback_preserves_prompt_and_history_atomicity(self):
        prompts.ensure_schema(self.conn)
        with self.assertRaisesRegex(RuntimeError, 'rollback'):
            with board.transaction(self.conn):
                prompts.save(self.conn, 'member', 'Uncommitted member', expected_version=0)
                self.assertEqual(prompts.get(self.conn, 'member')['version'], 1)
                raise RuntimeError('rollback')
        self.assertEqual(prompts.get(self.conn, 'member')['version'], 0)
        self.assertEqual(prompts.history(self.conn, 'member')['history'], [])


if __name__ == '__main__':
    unittest.main()
