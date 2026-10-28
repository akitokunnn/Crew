"""Persistent AI choices are explicit, transaction-safe and independent of models."""
import tests
from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from crew import board, runtimes, settings, store


def choice(runtime=None, model=None, effort=None):
    return {'runtime': runtime, 'model': model, 'effort': effort}


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='crew-settings-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home'

    @staticmethod
    def installed(*names):
        return lambda name: '/fixture/' + name if name in names else None

    def test_unconfigured_read_and_single_installed_runtime_do_not_create_home(self):
        self.assertEqual(settings.get(self.home), settings.DEFAULT)
        self.assertEqual(settings.resolve(self.home, 'digest', self.installed('codex')), choice('codex'))
        self.assertFalse(self.home.exists())
        value = settings.get(self.home)
        value['default']['runtime'] = 'claude'
        self.assertIsNone(settings.get(self.home)['default']['runtime'])

    def test_both_installed_need_a_choice_and_no_tools_has_specific_error(self):
        with self.assertRaisesRegex(settings.SettingsError, '设置页选择'):
            settings.resolve(self.home, which=self.installed('claude', 'codex'))
        with self.assertRaisesRegex(settings.SettingsError, '安装并登录 Codex CLI 或 Claude Code'):
            settings.resolve(self.home, which=self.installed())
        self.assertFalse(self.home.exists())

    def test_saved_home_options_survive_connections_and_task_null_follows_default(self):
        ai = {'default': choice('codex', 'future-provider/custom-model', 'future-effort'),
              'leader': None, 'member': None, 'digest': None, 'git': choice('claude'), 'operations': choice('codex', None, 'low-latency')}
        self.assertEqual(settings.save(self.home, ai), ai)
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(settings.load(conn), ai)
            self.assertEqual(settings.resolve(conn, 'digest', self.installed('codex', 'claude')), ai['default'])
            self.assertEqual(settings.resolve(conn, 'git', self.installed('codex', 'claude')), choice('claude'))
            self.assertEqual(settings.resolve(conn, 'operations', self.installed('codex', 'claude')), ai['operations'])
        self.assertEqual(settings.get(self.home), ai)

    def test_explicit_unavailable_runtime_never_silently_uses_other_tool(self):
        settings.save(self.home, {'default': choice('codex', 'my-model', 'my-effort')})
        discovery = mock.Mock(side_effect=self.installed('claude'))
        with self.assertRaisesRegex(settings.SettingsError, '已选择 codex.*未找到'):
            settings.resolve(self.home, 'digest', discovery)
        discovery.assert_called_once_with('codex')

    def test_operations_only_choice_resolves_without_a_global_default(self):
        settings.save(self.home, {'default': choice(), 'operations': choice('codex')})
        self.assertEqual(settings.resolve(self.home, 'operations', self.installed('codex', 'claude')), choice('codex'))
        self.assertIsNone(settings.get(self.home)['default']['runtime'])
        with self.assertRaises(settings.SettingsError):
            settings.resolve(self.home, 'default', self.installed('codex', 'claude'))

    def test_new_leader_has_an_independent_brain_and_old_settings_follow_default(self):
        settings.save(self.home, {'default': choice('codex', 'default-model')})
        self.assertIsNone(settings.get(self.home)['leader'])
        self.assertEqual(settings.resolve(self.home, 'leader', self.installed('codex', 'claude')), choice('codex', 'default-model'))
        settings.save(self.home, {'default': choice('codex', 'default-model'), 'leader': choice('claude', 'leader-model', 'high')})
        self.assertEqual(settings.resolve(self.home, 'leader', self.installed('codex', 'claude')), choice('claude', 'leader-model', 'high'))
        self.assertEqual(settings.resolve(self.home, 'digest', self.installed('codex', 'claude')), choice('codex', 'default-model'))
        with closing(store.connect(self.home)) as conn:
            existing = json.loads(conn.execute('SELECT ai_json FROM ai_settings').fetchone()[0])
            self.assertEqual(set(existing), {'default','digest','git','operations'})
            conn.execute('BEGIN IMMEDIATE')
            settings.save(conn, {**settings.get(conn), 'leader': None})
            self.assertIsNone(settings.get(conn)['leader'])
            conn.rollback()
        self.assertEqual(settings.get(self.home)['leader'], choice('claude', 'leader-model', 'high'))

    def test_explicit_task_is_whole_override_not_cross_runtime_model_or_effort_merge(self):
        settings.save(self.home, {'default': choice('claude', 'native-alias', 'future-effort'),
                                  'digest': choice('codex')})
        self.assertEqual(settings.resolve(self.home, 'digest', self.installed('claude', 'codex')), choice('codex'))
        with self.assertRaisesRegex(settings.SettingsError, '未知 AI 设置用途'):
            settings.resolve(self.home, 'invented-purpose', self.installed('codex'))

    def test_failure_saving_new_leader_choice_rolls_back_default_and_all_task_choices(self):
        original = settings.save(self.home, {'default': choice('claude'), 'leader': choice('codex')})
        with closing(store.connect(self.home)) as conn:
            conn.execute("CREATE TRIGGER reject_leader BEFORE INSERT ON ai_task_settings BEGIN SELECT RAISE(ABORT,'fixture write failure'); END")
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'fixture write failure'):
                settings.save(conn, {**original, 'default': choice('codex'), 'leader': choice('claude')})
            self.assertFalse(conn.in_transaction)
        self.assertEqual(settings.get(self.home), original)

    def test_save_and_resolve_use_callers_transaction_without_extra_connection_or_commit(self):
        original = {'default': choice('claude'), 'leader': None, 'member': None, 'digest': None, 'git': None, 'operations': None}
        settings.save(self.home, original)
        with closing(store.connect(self.home)) as conn:
            conn.execute('BEGIN IMMEDIATE')
            with mock.patch.object(store, 'connect', side_effect=AssertionError('No nested connection')):
                settings.save(conn, {'default': choice('codex', 'uncommitted-model')})
                self.assertEqual(settings.resolve(conn, 'git', self.installed('codex')), choice('codex', 'uncommitted-model'))
                self.assertTrue(conn.in_transaction)
            self.assertEqual(settings.get(self.home), original)
            conn.rollback()
            self.assertEqual(settings.load(conn), original)
            with board.transaction(conn):
                settings.save(conn, {'default': choice('codex')})
                self.assertEqual(runtimes.default_runtime(home=conn, purpose='operations', which=self.installed('codex')), 'codex')
        self.assertEqual(settings.get(self.home)['default'], choice('codex'))

    def test_invalid_settings_are_rejected_before_replacing_persisted_choices(self):
        saved = settings.save(self.home, {'default': choice('codex')})
        invalid = [None, {}, {'default': None}, {'default': choice(), 'extra': None},
                   {'default': {'runtime': 'codex'}}, {'default': choice('other')},
                   {'default': choice('codex', '')}, {'default': choice('codex', ' padded ')},
                   {'default': choice('codex', '--unsafe-flag')}, {'default': choice('codex', 'line\nbreak')},
                   {'default': choice('codex', None, 3)}, {'default': choice('codex'), 'operations': []}]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(settings.SettingsError):
                settings.save(self.home, value)
            self.assertEqual(settings.get(self.home), saved)

    def test_legacy_database_reads_without_migration_and_new_schema_preserves_other_data(self):
        self.home.mkdir()
        with closing(sqlite3.connect(store.db_path(self.home))) as conn:
            conn.execute('CREATE TABLE fixture_record(value TEXT)')
            conn.execute("INSERT INTO fixture_record VALUES('keep')")
            conn.commit()
        self.assertEqual(settings.get(self.home), settings.DEFAULT)
        settings.save(self.home, {'default': choice('codex')})
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(conn.execute('SELECT value FROM fixture_record').fetchone()[0], 'keep')
            self.assertEqual(settings.load(conn)['default'], choice('codex'))

    def test_default_runtime_wrapper_preserves_which_position_and_uses_home_purpose(self):
        self.assertEqual(runtimes.default_runtime(self.installed('codex')), 'codex')
        with self.assertRaisesRegex(board.BoardError, '设置页选择'):
            runtimes.default_runtime(self.installed('codex', 'claude'))
        settings.save(self.home, {'default': choice('claude'), 'operations': choice('codex')})
        self.assertEqual(runtimes.default_runtime(self.installed('claude', 'codex'), home=self.home), 'claude')
        self.assertEqual(runtimes.default_runtime(self.installed('claude', 'codex'), home=self.home, purpose='operations'), 'codex')

    def test_execution_defaults_are_public_schema_defaults_and_reads_create_nothing(self):
        expected = {'codex': 'workspace-write', 'claude': 'default'}
        self.assertEqual(settings.get_execution(self.home), expected)
        self.assertFalse(self.home.exists())
        with closing(store.connect(self.home)) as conn:
            columns = {row['name']: row['dflt_value'] for row in conn.execute('PRAGMA table_info(execution_settings)')}
            self.assertEqual(columns['codex'], "'workspace-write'")
            self.assertEqual(columns['claude'], "'default'")
            self.assertEqual(settings.get_execution(conn), expected)

    def test_execution_permissions_persist_separately_and_saving_starts_or_changes_no_runtime(self):
        ai = settings.save(self.home, {'default': choice('codex', 'chosen-model', 'chosen-effort')})
        value = {'codex': 'danger-full-access', 'claude': 'acceptEdits'}
        with mock.patch('subprocess.Popen', side_effect=AssertionError('Saving must not start/restart processes')):
            self.assertEqual(settings.save_execution(self.home, value), value)
        self.assertEqual(settings.get(self.home), ai)
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(settings.get_execution(conn), value)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM session_runtimes').fetchone()[0], 0)
        loaded = settings.get_execution(self.home)
        loaded['codex'] = 'read-only'
        self.assertEqual(settings.get_execution(self.home), value)

    def test_invalid_execution_modes_leave_saved_values_unchanged(self):
        saved = settings.save_execution(self.home, {'codex': 'read-only', 'claude': 'default'})
        for value in (None, {}, {'codex': 'read-only'}, {'codex': 'read-only', 'claude': 'default', 'extra': True},
                      {'codex': 'bypassPermissions', 'claude': 'default'}, {'codex': 'workspace-write', 'claude': 'danger-full-access'},
                      {'codex': None, 'claude': 'default'}, {'codex': True, 'claude': 'default'}):
            with self.subTest(value=value), self.assertRaises(settings.SettingsError):
                settings.save_execution(self.home, value)
            self.assertEqual(settings.get_execution(self.home), saved)

    def test_execution_save_participates_in_callers_transaction_without_restart_or_nested_connection(self):
        saved = settings.save_execution(self.home, {'codex': 'workspace-write', 'claude': 'bypassPermissions'})
        with closing(store.connect(self.home)) as conn:
            conn.execute('BEGIN IMMEDIATE')
            with mock.patch.object(store, 'connect', side_effect=AssertionError('No extra connection')):
                settings.save_execution(conn, {'codex': 'read-only', 'claude': 'default'})
                self.assertEqual(settings.get_execution(conn), {'codex': 'read-only', 'claude': 'default'})
                self.assertTrue(conn.in_transaction)
            self.assertEqual(settings.get_execution(self.home), saved)
            conn.rollback()
        self.assertEqual(settings.get_execution(self.home), saved)


if __name__ == '__main__':unittest.main()
