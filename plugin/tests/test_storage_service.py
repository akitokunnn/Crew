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
