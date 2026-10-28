"""Scope resolution, permission inheritance and optimistic writes use crew.db."""
import tests
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from crew import board, settings, store


def choice(runtime=None, model=None, effort=None):
    return {'runtime': runtime, 'model': model, 'effort': effort}


class SettingsScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='crew-settings-scopes-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.conn = store.connect(self.home)
        self.addCleanup(self.conn.close)
        for name in ('one', 'two'):
            folder = self.root / name
            folder.mkdir()
            board.init_project(self.conn, name, folder)
            board.register_session(self.conn, 'session-' + name, name, str(folder), None, None, name='leader', role='leader')

    def test_reading_explains_defaults_without_creating_scope_rows_or_discovering_tools(self):
        previous_changes = self.conn.total_changes
        with mock.patch('shutil.which', side_effect=AssertionError('Read must not discover or start CLI')):
            value = settings.effective(self.conn, session_id='session-one', purpose='member')
        self.assertEqual(value['ai'], choice())
        self.assertEqual(value['execution'], settings.EXECUTION_DEFAULT)
        self.assertEqual(value['sources']['ai']['runtime']['scope'], 'builtin-role-preference')
        self.assertEqual(value['role_preference']['runtime'], 'codex')
        self.assertEqual(value['role_preference']['availability'], 'not_inspected')
        self.assertEqual(value['applies'], 'next_launch')
        self.assertFalse(value['current_execution_changed'])
        self.assertEqual(value['native_configuration']['loaded'], 'not_confirmed')
        self.assertEqual(self.conn.total_changes, previous_changes)

    def test_global_project_session_and_role_values_with_native_model_identifiers(self):
        global_value = settings.save_scope(self.conn, ai={'default': choice('claude', 'native-future', 'native-effort'),
                                                            'member': choice('codex', 'provider/member', 'unlisted-effort')},
                                           execution={'claude': 'acceptEdits'}, expected_version=0)
        project_value = settings.save_scope(self.conn, 'project', 'one', ai={'default': choice('codex')},
                                           execution={'codex': 'read-only'}, expected_version=0)
        settings.save_scope(self.conn, 'session', 'session-one', ai={'leader': choice('claude', 'custom/leader', 'dynamic')},
                            execution={'claude': 'bypassPermissions'}, expected_version=0)
        value = settings.effective(self.conn, session_id='session-one', purpose='leader')
        self.assertEqual(value['ai'], choice('claude', 'custom/leader', 'dynamic'))
        self.assertEqual(value['execution'], {'codex': 'read-only', 'claude': 'bypassPermissions'})
        self.assertEqual(value['sources']['ai']['model'], {'scope': 'session', 'identity': 'session-one', 'purpose': 'leader'})
        self.assertEqual(value['sources']['execution']['codex']['scope'], 'project')
        member = settings.effective(self.conn, session_id='session-one', purpose='member')
        self.assertEqual(member['ai'], choice('codex'))
        other = settings.effective(self.conn, session_id='session-two', purpose='member')
        self.assertEqual(other['ai'], choice('codex', 'provider/member', 'unlisted-effort'))
        self.assertEqual(other['execution']['codex'], 'workspace-write')
        self.assertEqual(global_value['version'], 1)
        self.assertEqual(project_value['version'], 1)

    def test_removing_overrides_reveals_parent_without_leaking_model_between_carriers(self):
        settings.save(self.conn, {'default': choice('claude', 'custom-old', 'custom-old-effort')})
        value = settings.save_scope(self.conn, 'project', 'one', ai={'default': choice('codex')}, expected_version=0)
        self.assertEqual(settings.effective(self.conn, project='one')['ai'], choice('codex'))
        value = settings.save_scope(self.conn, 'project', 'one', ai={'default': None}, expected_version=value['version'])
        self.assertEqual(value['ai'], {})
        self.assertEqual(settings.effective(self.conn, project='one')['ai'], choice('claude', 'custom-old', 'custom-old-effort'))

    def test_stale_save_and_legacy_global_save_invalidate_cas_and_preserve_user_content(self):
        first = settings.save_scope(self.conn, ai={'default': choice('codex', 'user-model')}, expected_version=0)
        with self.assertRaises(board.Conflict):
            settings.save_scope(self.conn, ai={'default': choice('claude', 'ai-stale')}, expected_version=0)
        settings.save(self.conn, {'default': choice('claude', 'legacy-user-model')})
        with self.assertRaises(board.Conflict):
            settings.save_scope(self.conn, ai={'default': choice('codex', 'stale')}, expected_version=first['version'])
        self.assertEqual(settings.get(self.conn)['default']['model'], 'legacy-user-model')
        before_execution = settings.get_scope(self.conn)['version']
        settings.save_execution(self.conn, {'codex': 'read-only', 'claude': 'default'})
        self.assertEqual(settings.get_scope(self.conn)['version'], before_execution + 1)

    def test_reset_is_scope_local_and_does_not_restart_current_execution(self):
        settings.save_scope(self.conn, ai={'default': choice('codex')}, expected_version=0)
        scope = settings.save_scope(self.conn, 'project', 'one', ai={'default': choice('claude')},
                                    execution={'codex': 'danger-full-access'}, expected_version=0)
        with mock.patch('subprocess.Popen', side_effect=AssertionError('Never restart while saving')):
            reset = settings.save_scope(self.conn, 'project', 'one', reset=True, expected_version=scope['version'])
        self.assertEqual(reset['ai'], {})
        self.assertEqual(reset['execution'], {})
        self.assertEqual(settings.effective(self.conn, project='one')['ai'], choice('codex'))
        self.assertEqual(settings.effective(self.conn, project='one')['execution']['codex'], 'workspace-write')

    def test_invalid_scope_permissions_cross_workspace_and_missing_versions_do_not_write(self):
        for options in ({'expected_version': True}, {'expected_version': -1}, {},
                        {'expected_version': 0, 'ai': {'unknown': choice()}},
                        {'expected_version': 0, 'execution': {'codex': 'bypassPermissions'}},
                        {'expected_version': 0, 'ai': {'default': {'runtime': 'codex'}}}):
            with self.subTest(options=options), self.assertRaises(settings.SettingsError):
                settings.save_scope(self.conn, 'project', 'one', **options)
        with self.assertRaises(board.BoardError):
            settings.save_scope(self.conn, 'project', 'missing', ai={'default': choice()}, expected_version=0)
        with self.assertRaises(settings.SettingsError):
            settings.effective(self.conn, project='two', session_id='session-one')
        with self.assertRaises(settings.SettingsError):
            settings.get_scope(self.conn, 'global', 'session-one')
        with self.assertRaises(settings.SettingsError):
            settings.get_scope(self.conn, 'project', None)

    def test_caller_transaction_rolls_back_value_and_revision_without_nested_connect(self):
        settings.ensure_schema(self.conn)
        with self.assertRaisesRegex(RuntimeError, 'rollback'):
            with board.transaction(self.conn):
                with mock.patch.object(store, 'connect', side_effect=AssertionError('No second DB')):
                    settings.save_scope(self.conn, 'project', 'one', ai={'default': choice('codex')}, expected_version=0)
                self.assertEqual(settings.effective(self.conn, project='one')['ai'], choice('codex'))
                raise RuntimeError('rollback')
        self.assertEqual(settings.get_scope(self.conn, 'project', 'one')['version'], 0)
        self.assertEqual(settings.effective(self.conn, project='one')['ai'], choice())

    def test_two_connections_racing_to_save_one_version_preserve_one_whole_selection(self):
        settings.ensure_schema(self.conn)
        ready = threading.Barrier(2)

        def write(model):
            with closing(store.connect(self.home)) as conn:
                ready.wait(timeout=5)
                try:
                    value = settings.save_scope(conn, 'project', 'one', ai={'default': choice('codex', model, model)}, expected_version=0)
                    return 'saved', value['ai']['default']
                except board.Conflict:
                    return 'conflict', None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(write, ('first-model', 'second-model')))
        self.assertEqual(sorted(result[0] for result in results), ['conflict', 'saved'])
        saved = next(result[1] for result in results if result[0] == 'saved')
        self.assertEqual(settings.effective(self.conn, project='one')['ai'], saved)
        self.assertEqual(saved['model'], saved['effort'])
        self.assertEqual(settings.get_scope(self.conn, 'project', 'one')['version'], 1)

    def test_readonly_home_and_resolution_use_real_config_and_report_missing_runtime(self):
        settings.save_scope(self.conn, 'project', 'one', ai={'default': choice('codex', 'provider/x', 'future')}, expected_version=0)
        value = settings.effective(self.home, project='one')
        self.assertEqual(value['ai'], choice('codex', 'provider/x', 'future'))
        with self.assertRaisesRegex(settings.SettingsError, '已选择 codex.*未找到'):
            settings.resolve(self.home, project='one', which=lambda name: '/fixture/claude' if name == 'claude' else None)
        self.assertEqual(settings.resolve(self.home, project='one', which=lambda name: '/fixture/' + name), value['ai'])
        missing = self.root / 'no-home'
        self.assertEqual(settings.effective(missing)['execution'], settings.EXECUTION_DEFAULT)
        self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
