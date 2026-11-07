"""Every model-facing instruction is one editable catalog entry, shown with its use."""
import tests
from pathlib import Path
import tempfile
import unittest

from crew import behavior_prompts as prompts
from crew import board, goal_context, goal_mode, goals, settings, store, trust_mode
from crew.web_api import behavior_prompts as prompt_api

PLUGIN = Path(prompts.__file__).resolve().parents[1]


class PromptCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='crew-prompt-catalog-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)
        folder = Path(self.temp.name) / 'one'
        folder.mkdir()
        board.init_project(self.conn, 'one', folder)
        board.register_session(self.conn, 'leader-one', 'one', str(folder), None, None, name='lead', role='leader')

    def test_each_entry_has_a_file_group_description_and_declared_placeholders(self):
        for spec in prompts.SPECS:
            with self.subTest(key=spec.key):
                self.assertTrue((PLUGIN / spec.path).is_file())
                self.assertIn(spec.group, prompts.GROUPS)
                self.assertTrue(spec.purpose and spec.receivers and spec.timing)
                self.assertEqual(prompts.placeholders(prompts.DEFAULTS[spec.key]), set(spec.placeholders))
                self.assertLessEqual(len(prompts.DEFAULTS[spec.key]), spec.max_length)

    def test_role_bundles_share_rules_instead_of_repeating_them(self):
        for role, keys in prompts.BUNDLES.items():
            self.assertEqual(keys[:2], ('common', role))
            self.assertTrue(set(keys) <= set(prompts.KEYS))
        self.assertIn('final_report', prompts.bundle('leader'))
        self.assertNotIn('final_report', prompts.bundle('member'))
        self.assertIn('member_boundary', prompts.bundle('member'))
        # Each rule lives in exactly one default text.
        for phrase in ('crew goal report-template', 'archived 是“仅归档', 'operation_key', 'known_versions'):
            owners = [key for key, text in prompts.DEFAULTS.items() if phrase in text]
            self.assertEqual(len(owners), 1, (phrase, owners))
        for name in ('leader.md', 'git.md'):
            self.assertLess(len((PLUGIN / 'agents' / name).read_text(encoding='utf-8')), 500)

    def test_placeholders_are_validated_on_save_and_filled_on_use(self):
        with self.assertRaisesRegex(settings.SettingsError, 'cwd'):
            prompts.save(self.conn, 'execution_identity', '你是 {{cwd}}', expected_version=0)
        prompts.save(self.conn, 'execution_identity', '执行者 {{crew_name}}（{{ session_id }}）', expected_version=0)
        value = prompts.text(self.conn, 'execution_identity', session_id='leader-one',
                             values={'crew_name': '甲', 'session_id': 'leader-one'})
        self.assertEqual(value, '执行者 甲（leader-one）')
        with self.assertRaisesRegex(settings.SettingsError, 'crew_name'):
            prompts.text(self.conn, 'execution_identity', session_id='leader-one', values={'session_id': 'x'})

    def test_protected_goal_protocol_cannot_exceed_its_context_budget(self):
        limit = goal_context.DEFAULT_LIMITS['base']
        self.assertEqual(prompts.SPEC['goal_protocol'].max_length, limit)
        with self.assertRaisesRegex(settings.SettingsError, str(limit)):
            prompts.save(self.conn, 'goal_protocol', 'x' * (limit + 1), expected_version=0)

    def test_edited_mode_and_trust_text_reach_the_host_and_change_material_version(self):
        goal = goals.create(self.conn, 'one', 'leader-one', 'T', 'Work', ['Done'])
        before = goal_mode.context(self.conn, goal['id'])
        current = goal_mode.get(self.conn, goal['id'])['mode']
        key = 'goal_autonomous' if current == 'autonomous' else 'goal_human'
        prompts.save(self.conn, key, '自定义任务模式说明', expected_version=0)
        after = goal_mode.context(self.conn, goal['id'])
        self.assertNotEqual(before, after)
        self.assertIn('自定义任务模式说明', after)
        trust_mode.set(self.conn, 'one', True, '交付网站', leader_session_id='leader-one')
        prompts.save(self.conn, 'trust_on', '目标是 {{goal}}，自行推进', expected_version=0)
        self.assertIn('目标是 交付网站，自行推进', trust_mode.context(self.conn, 'leader-one'))

    def test_list_api_returns_groups_and_usage_metadata(self):
        value = prompt_api.get(self.home, {'action': 'list'})
        self.assertEqual([group['id'] for group in value['groups']], list(prompts.GROUPS))
        self.assertEqual([item['key'] for item in value['items']], list(prompts.KEYS))
        item = next(row for row in value['items'] if row['key'] == 'final_report')
        self.assertEqual(item['group'], 'delivery')
        self.assertEqual(item['file'], 'plugin/prompts/final_report.md')
        self.assertIn('leader', item['receivers'])


if __name__ == '__main__':
    unittest.main()
