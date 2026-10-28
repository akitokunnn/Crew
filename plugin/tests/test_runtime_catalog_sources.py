"""Source-aware native catalog fixtures; no native model or account is used."""
import tests
from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from crew import board, models, runtimes, settings, store
from crew.claude_events import ClaudeEvents
from crew.runtime_catalog import Catalog
from crew.session_journal import Journal


class RuntimeCatalogSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='crew-catalog-sources-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.claude, self.codex = self.root / '.claude', self.root / '.codex'
        self.claude.mkdir()
        self.codex.mkdir()
        self.factory = mock.MagicMock()

    def catalog(self, **options):
        return Catalog(client_factory=self.factory, which=lambda name: name,
                       claude_config_root=self.claude, codex_config_root=self.codex, **options)

    def test_unconfigured_catalog_is_empty_and_snapshot_never_discovers_or_uses_machine_home(self):
        with mock.patch('pathlib.Path.home', side_effect=AssertionError('Do not use machine native sources')):
            catalog = Catalog(client_factory=self.factory, which=lambda name: name)
            rows = catalog.snapshot()
        self.assertEqual([(item['models'], item['efforts']) for item in rows], [([], []), ([], [])])
        self.assertEqual(rows[0]['discovery']['status'], 'unsupported')
        self.assertEqual(rows[1]['discovery']['status'], 'not_requested')
        self.assertEqual(rows[0]['loaded']['status'], 'not_confirmed')
        self.assertEqual(rows[0]['configuration_entry']['action'], 'settings')
        self.factory.assert_not_called()

    def test_native_configuration_is_evidence_of_configuration_not_loading_and_never_exposes_commands(self):
        (self.claude / 'settings.json').write_text(json.dumps({'model': 'my-native-model', 'effortLevel': 'high',
            'mcpServers': {'local-tools': {'command': 'SECRET-COMMAND', 'env': {'TOKEN': 'SECRET-TOKEN'}}},
            'enabledPlugins': {'crew@local': True}}), encoding='utf-8')
        skill = self.claude / 'skills' / 'review'
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('SECRET SKILL BODY', encoding='utf-8')
        (self.claude / 'CLAUDE.md').write_text('PRIVATE PROJECT BODY', encoding='utf-8')
        (self.codex / 'config.toml').write_text('model="provider-codex"\nmodel_reasoning_effort="max"\n'
            '[mcp_servers.sample]\ncommand="SECRET-MCP-COMMAND"\n', encoding='utf-8')
        claude, codex = self.catalog().snapshot()
        self.assertEqual(claude['models'][0]['id'], 'my-native-model')
        self.assertEqual(claude['models'][0]['status'], 'configured')
        self.assertNotIn('efforts', claude['models'][0])
        self.assertEqual(claude['efforts'], ['high'])
        self.assertCountEqual([item['kind'] for item in claude['capabilities']], ['mcp', 'plugin', 'skill', 'project_instructions'])
        self.assertEqual({item['kind']: item['status'] for item in claude['capabilities']},
                         {'mcp': 'configured', 'plugin': 'configured', 'skill': 'discovered', 'project_instructions': 'discovered'})
        self.assertEqual(claude['loaded']['status'], 'not_confirmed')
        self.assertEqual(codex['models'][0]['id'], 'provider-codex')
        self.assertNotIn('SECRET', json.dumps([claude, codex]))
        self.assertNotIn('PRIVATE PROJECT BODY', json.dumps([claude, codex]))

    def test_invalid_native_config_error_remains_visible_without_replacing_user_choice(self):
        (self.claude / 'settings.json').write_text('{bad config', encoding='utf-8')
        rows = self.catalog(configured_provider=lambda: [{'runtime': 'claude', 'model': 'explicit-choice',
            'effort': None, 'source': {'kind': 'user_selection'}}]).snapshot()
        self.assertEqual(rows[0]['models'][0]['id'], 'explicit-choice')
        self.assertIn('JSONDecodeError', rows[0]['configuration_errors'][0])
        self.assertTrue(rows[0]['available'])
        self.factory.assert_not_called()

    def test_crew_choices_are_readonly_include_role_scope_and_preserve_custom_names(self):
        home = self.root / 'crew-home'
        project = self.root / 'project'
        project.mkdir()
        with closing(store.connect(home)) as conn:
            board.init_project(conn, 'demo', project)
            board.join(conn, 'member-id', 'demo', 'worker', project, None, None)
            settings.save(conn, {'default': {'runtime': 'claude', 'model': 'native-default', 'effort': None}})
            settings.save_scope(conn, 'project', 'demo', ai={'member': {'runtime': 'codex', 'model': 'workspace-brain', 'effort': 'high'}}, expected_version=0)
            runtimes.set(conn, 'member-id', 'codex')
            models.set(conn, 'member-id', 'chosen-by-user', 'max', runtime='codex')
        with mock.patch('crew.store.connect', side_effect=AssertionError('Catalog cannot migrate or create storage')):
            claude, codex = self.catalog(home=home).snapshot()
        self.assertIn('native-default', [item['id'] for item in claude['models']])
        self.assertCountEqual([item['id'] for item in codex['models']], ['workspace-brain', 'chosen-by-user'])
        self.assertTrue(any(item.get('source', {}).get('scope') == 'project' for item in codex['configured']))

    def test_model_rpc_advertises_only_actual_efforts_and_records_discovery_failure(self):
        native = self.factory.return_value.__enter__.return_value
        native.list_models.return_value = {'data': [{'id': 'real-native', 'displayName': 'Actual model',
            'supportedReasoningEfforts': [{'reasoningEffort': 'future-native-effort'}], 'contextWindow': 321000}], 'nextCursor': None}
        catalog = self.catalog()
        rows = catalog.refresh()
        self.assertEqual(rows[1]['models'][0]['status'], 'discovered')
        self.assertEqual(rows[1]['efforts'], ['future-native-effort'])
        self.assertEqual(rows[1]['models'][0]['context_window'], 321000)
        self.factory.assert_called_once_with(timeout=15)
        catalog.snapshot()
        native.list_models.assert_called_once_with()
        native.list_models.side_effect = OSError('actual native discovery failure')
        failed = catalog.refresh(force=True)[1]
        self.assertEqual(failed['discovery']['status'], 'failed')
        self.assertIn('actual native discovery failure', failed['discovery']['error'])
        self.assertTrue(failed['available'])
        self.assertEqual(failed['models'][0]['id'], 'real-native')

    def test_repeat_native_cursor_is_a_bounded_visible_failure(self):
        native = self.factory.return_value.__enter__.return_value
        native.list_models.return_value = {'data': [], 'nextCursor': 'same-cursor'}
        rows = self.catalog().refresh()
        self.assertEqual(native.list_models.call_count, 2)
        self.assertEqual(rows[1]['discovery']['status'], 'failed')

    def test_native_execution_model_is_observed_without_claiming_skill_loading(self):
        entry = {'runtime': 'claude', 'execution_id': 'observed', 'model': 'actual-configured-model', 'effort': 'high',
                 'source': {'kind': 'native_execution', 'protocol': 'claude:get_settings'}, 'capabilities_loaded': 'not_confirmed'}
        catalog = self.catalog(loaded_provider=lambda: [entry])
        row = catalog.snapshot()[0]
        self.assertEqual(row['models'][0]['status'], 'observed')
        self.assertEqual(row['loaded']['executions'][0]['capabilities_loaded'], 'not_confirmed')
        self.assertEqual(row['configured'], [])

    def test_model_names_do_not_predict_runtime_or_effort_capability(self):
        models.validate_options('claude-haiku-4-5-20251001', 'max')
        models.validate_options('claude-custom-provider', 'high', runtime='codex')
        models.validate_options('any-name', 'future-native-effort', runtime='claude',
                                catalog=[{'id': 'any-name', 'efforts': ['future-native-effort']}])
        with self.assertRaises(ValueError):
            models.validate_options('any-name', 'max', runtime='claude', catalog=[{'id': 'any-name', 'efforts': ['high']}])

    def test_native_init_capability_record_keeps_only_reported_names_and_status(self):
        journal = Journal(self.root / 'history.jsonl')
        ClaudeEvents(journal).consume({'type': 'system', 'subtype': 'init', 'session_id': 'native-sample',
            'tools': ['Read'], 'skills': ['review'], 'plugins': [{'name': 'crew', 'path': 'SECRET-PATH'}],
            'mcp_servers': [{'name': 'local-tools', 'status': 'connected', 'command': 'SECRET-COMMAND'}]})
        evidence = journal.events[-1]
        self.assertEqual(evidence['type'], 'native_capabilities')
        self.assertIn({'kind': 'mcp', 'name': 'local-tools', 'status': 'connected'}, evidence['capabilities'])
        self.assertIn({'kind': 'skill', 'name': 'review', 'status': 'reported'}, evidence['capabilities'])
        self.assertNotIn('SECRET', json.dumps(evidence))


if __name__ == '__main__':
    unittest.main()
