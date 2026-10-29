"""The user-visible storage flow must retain history without starting model work."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from crew import board, store
from crew.storage_service import StorageService
from crew.storage_v2 import Repository


class StorageServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.project = self.root / '股票预警'
        self.project.mkdir()
        with closing(store.connect(self.home)) as conn:
            board.init_project(conn, '股票预警', self.project)
            conn.execute("INSERT INTO sessions(session_id,project,crew_name,role,status,started_at,cwd) VALUES(?,?,?,?,?,?,?)",
                ('legacy-native-id','股票预警','Leader','leader','offline',store.now_iso(),str(self.project)))
        self.storage = StorageService(self.home, isolated=True)

    def test_discovery_is_read_only_and_does_not_choose_or_upgrade(self):
        before = hashlib.sha256((self.home/'crew.db').read_bytes()).hexdigest()
        with mock.patch('subprocess.Popen', side_effect=AssertionError('data operation started a process')):
            found = self.storage.discover(self.project)
        self.assertEqual(found['status'], 'upgrade_required')
        self.assertEqual(found['candidates'][0]['counts']['sessions'], 1)
        self.assertFalse((self.project/'.crew/workspace.json').exists())
        self.assertEqual(before,hashlib.sha256((self.home/'crew.db').read_bytes()).hexdigest())
        with closing(self.storage.repository.connect()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM workspaces').fetchone()[0],0)

    def test_unknown_selection_never_opens_an_empty_workspace(self):
        with self.assertRaisesRegex(ValueError,'选择'):
            self.storage.open({'root':str(self.project),'action':'upgrade','candidate_id':'missing'})
        self.assertFalse((self.project/'.crew/workspace.json').exists())

    def test_marker_without_history_is_a_recoverable_error(self):
        marker=self.project/'.crew/workspace.json';marker.parent.mkdir()
        marker.write_text(json.dumps({'schema_version':2,'workspace_id':'fa5c8ac4-f939-4b7a-8625-51fbc796562d'}))
        self.assertEqual(self.storage.discover(self.project)['status'],'history_missing')
        with self.assertRaisesRegex(ValueError,'导入'):
            self.storage.open({'root':str(self.project),'action':'upgrade'})

    def test_newer_marker_never_creates_empty_registration(self):
        marker=self.project/'.crew/workspace.json';marker.parent.mkdir()
        marker.write_text(json.dumps({'schema_version':3,'workspace_id':'fa5c8ac4-f939-4b7a-8625-51fbc796562d'}))
        with self.assertRaisesRegex(ValueError,'更新'):
            self.storage.discover(self.project)

    def test_fresh_v2_opens_a_v1_folder_without_importing_or_replacing_history(self):
        # Same folder, separate HOME: the exact pattern used by V1 and V2.
        old = self.storage.open({'root': str(self.project), 'action': 'upgrade',
            'candidate_id': self.storage.discover(self.project)['candidates'][0]['id']})
        marker = self.project / '.crew/workspace.json'
        original_marker = marker.read_bytes()
        original_db = (self.home / 'crew.db').read_bytes()
        fresh_home = self.root / 'v2-home'
        with closing(store.connect(fresh_home)):
            pass
        fresh = StorageService(fresh_home, isolated=True)
        found = fresh.discover(self.project)
        self.assertEqual(found['status'], 'history_missing')
        self.assertTrue(found['can_start_fresh'])
        with mock.patch('subprocess.Popen', side_effect=AssertionError('Opening starts no AI')):
            result = fresh.open({'root': str(self.project), 'action': 'fresh'})
            again = fresh.open({'root': str(self.project)})
            duplicate = fresh.open({'root': str(self.project), 'action': 'fresh'})
        self.assertEqual(result['model_calls'], 0)
        self.assertNotEqual(result['workspace_id'], old['workspace_id'])
        self.assertEqual(result['workspace_id'], again['workspace_id'])
        self.assertEqual(duplicate['project']['workspace_id'], result['workspace_id'])
        self.assertEqual(marker.read_bytes(), original_marker)
        self.assertEqual((self.home / 'crew.db').read_bytes(), original_db)
        self.assertEqual(fresh.history(result['workspace_id'])['agents'], [])
        with closing(store.connect(fresh_home)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)
        separate = self.project / '.crew/workspace-v2.json'
        self.assertEqual(json.loads(separate.read_text('utf-8'))['workspace_id'], result['workspace_id'])
        # A package restored onto a copy of this folder must use the V2 marker.
        from crew.storage_v2 import activation
        self.assertEqual(activation._marker(self.project, result['workspace_id']), separate)
        # Exercise package fork + actual activation with both marker files.
        import shutil
        package = self.root / 'fresh.crewpack'
        fresh.export({'project': result['project']['name'], 'destination': str(package)})
        clone = self.root / 'copied-project'
        shutil.copytree(self.project, clone)
        target_home = self.root / 'restored-home'
        target = StorageService(target_home, isolated=True)
        imported = target.import_package({'path': str(package), 'mode': 'fork'})
        with mock.patch('subprocess.Popen', side_effect=AssertionError('Restoring starts no AI')):
            activated = activation.activate_workspace(target.repository, imported['workspace_id'],
                target_home, clone, replace_source_marker=True)
            reopened = target.open({'root': str(clone)})
        self.assertEqual(reopened['workspace_id'], imported['workspace_id'])
        self.assertEqual(activated['workspace_id'], imported['workspace_id'])
        self.assertEqual((clone / '.crew/workspace.json').read_bytes(), original_marker)
        self.assertEqual(json.loads(separate.read_text('utf-8'))['workspace_id'], result['workspace_id'])

    def test_fresh_marker_permission_failure_creates_no_project_and_retries_same_identity(self):
        marker = self.project / '.crew/workspace.json'
        marker.parent.mkdir()
        marker.write_text(json.dumps({'format': 'crew-workspace', 'schema_version': 2,
            'workspace_id': 'fa5c8ac4-f939-4b7a-8625-51fbc796562d'}))
        original = marker.read_bytes()
        fresh_home = self.root / 'fresh-permission'
        with closing(store.connect(fresh_home)):
            pass
        fresh = StorageService(fresh_home, isolated=True)
        native_open = Path.open
        def fail_new_marker(path, mode='r', *args, **kwargs):
            if path.name == 'workspace-v2.json' and mode == 'x':
                raise PermissionError('Injected marker permission failure')
            return native_open(path, mode, *args, **kwargs)
        with mock.patch.object(Path, 'open', fail_new_marker):
            with self.assertRaisesRegex(ValueError, '无法建立独立'):
                fresh.open({'root': str(self.project), 'action': 'fresh'})
        pending = fresh._fresh_intent(self.project)
        with closing(store.connect(fresh_home)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM projects').fetchone()[0], 0)
        self.assertEqual(marker.read_bytes(), original)
        recovered = StorageService(fresh_home, isolated=True).open({'root': str(self.project), 'action': 'fresh'})
        self.assertEqual(recovered['workspace_id'], pending['workspace_id'])
        self.assertEqual(fresh.discover(self.project)['status'], 'ready')
        self.assertFalse(fresh._fresh_intent_path(self.project).exists())

    def test_fresh_interrupted_after_marker_is_explicitly_resumable_without_new_identity(self):
        marker = self.project / '.crew/workspace.json'
        marker.parent.mkdir()
        marker.write_text(json.dumps({'format': 'crew-workspace', 'schema_version': 2,
            'workspace_id': 'fa5c8ac4-f939-4b7a-8625-51fbc796562d'}))
        fresh_home = self.root / 'interrupted-fresh'
        with closing(store.connect(fresh_home)):
            pass
        fresh = StorageService(fresh_home, isolated=True)
        with mock.patch.object(fresh, 'refresh', side_effect=OSError('Interrupted after registration')):
            with self.assertRaisesRegex(OSError, 'Interrupted after registration'):
                fresh.open({'root': str(self.project), 'action': 'fresh'})
        expected = fresh._fresh_intent(self.project)['workspace_id']
        restarted = StorageService(fresh_home, isolated=True)
        self.assertTrue(restarted.discover(self.project)['can_start_fresh'])
        recovered = restarted.open({'root': str(self.project), 'action': 'fresh'})
        self.assertEqual(recovered['workspace_id'], expected)
        with closing(store.connect(fresh_home)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM projects').fetchone()[0], 1)
        self.assertEqual(restarted.discover(self.project)['status'], 'ready')

    def test_fresh_cannot_replace_missing_v2_history_or_current_local_history(self):
        separate = self.project / '.crew/workspace-v2.json'
        separate.parent.mkdir()
        separate.write_text(json.dumps({'schema_version': 2,
            'workspace_id': 'fa5c8ac4-f939-4b7a-8625-51fbc796562d'}))
        with self.assertRaisesRegex(ValueError, '不能覆盖'):
            self.storage.open({'root': str(self.project), 'action': 'fresh'})
        self.assertEqual(self.storage.discover(self.project)['status'], 'history_missing')

    def test_isolated_home_never_discovers_formal_sources(self):
        extra=self.root/'other';extra.mkdir()
        with mock.patch.object(store,'default_home',return_value=self.home):
            service=StorageService(self.home,isolated=True,legacy_homes=[extra])
            self.assertEqual(service._homes(),[self.home.resolve()])

    def test_history_is_paginated_and_cannot_return_another_workspace(self):
        repo=Repository(self.home)
        a=repo.create_workspace('a');b=repo.create_workspace('b')
        result=self.storage.history(a['id'])
        self.assertEqual(result['workspace']['name'],'a')
        self.assertEqual(result['agents'],[])
        self.assertEqual(result['model_calls'],0)
        self.assertNotIn(b['id'],json.dumps(result))

    def test_upgrade_and_reopen_preserve_identity_and_never_spawn(self):
        found=self.storage.discover(self.project)
        with mock.patch('subprocess.Popen',side_effect=AssertionError('migration started a process')):
            result=self.storage.open({'root':str(self.project),'action':'upgrade','candidate_id':found['candidates'][0]['id']})
            again=self.storage.open({'root':str(self.project)})
        self.assertEqual(result['workspace_id'],again['workspace_id'])
        marker=json.loads((self.project/'.crew/workspace.json').read_text())
        self.assertEqual(marker['workspace_id'],result['workspace_id'])
        history=self.storage.history(result['workspace_id'])
        self.assertEqual([a['name'] for a in history['agents']],['Leader'])


if __name__ == '__main__':
    unittest.main()
