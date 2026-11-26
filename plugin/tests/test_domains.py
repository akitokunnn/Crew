"""Architecture board domain tree: isolated homes, temporary projects and an isolated HTTP service."""
import tests  # Install the production-service connection guard.
import uuid
from contextlib import closing
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from crew.web_api import routes
from crew import board, domains, remote_routes, session_ops, store, web
from test_shell import NOW, PLUGIN, ShellFixture


class DomainTreeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='crew-domains-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.conn = store.connect(self.base / 'home')
        self.addCleanup(self.conn.close)
        for name in ('demo', 'other'):
            (self.base / name).mkdir()
            board.init_project(self.conn, name, self.base / name, now=NOW)
        for sid, name in (('cam', '相机'), ('depth', '深度'), ('vision', '视觉'), ('voice', '声纹')):
            self.leader(sid, name)
        board.join(self.conn, 'worker', 'demo', 'worker', self.base / 'demo', self.base / 'demo', 'main', now=NOW)
        board.join(self.conn, 'foreign', 'other', 'foreign', self.base / 'other', self.base / 'other', 'main',
                   role='leader', now=NOW)

    def leader(self, sid, name):
        board.join(self.conn, sid, 'demo', name, self.base / 'demo', self.base / 'demo', 'main', role='leader', now=NOW)

    def apply(self, *ops, base=None, project='demo'):
        return domains.apply(self.conn, project, list(ops), base, now=NOW)

    def create(self, part_id, name, parent=None, x=0, y=0):
        return {'op': 'create', 'id': part_id, 'name': name, 'parent_id': parent, 'x': x, 'y': y}

    def tree(self):
        """感知 › 视觉 › 相机; 视觉 holds 视觉 (leading) and 深度, 相机 holds 相机."""
        self.apply(self.create('sense01', '感知'), self.create('sight01', '视觉', 'sense01', 0, 160),
                   self.create('camera1', '相机', 'sight01', 0, 320),
                   {'op': 'assign', 'session_id': 'vision', 'domain_id': 'sight01'},
                   {'op': 'assign', 'session_id': 'depth', 'domain_id': 'sight01'},
                   {'op': 'lead', 'session_id': 'vision', 'lead': True},
                   {'op': 'assign', 'session_id': 'cam', 'domain_id': 'camera1'})

    def parts(self):
        return {part['id']: part for part in domains.snapshot(self.conn, 'demo')['parts']}

    def places(self):
        return {item['session_id']: (item['domain_id'], item['lead']) for item in domains.snapshot(self.conn, 'demo')['leaders']}

    def test_tree_paths_layout_direction_and_version(self):
        self.assertEqual(domains.snapshot(self.conn, 'demo'),
                         {'project': 'demo', 'version': 0, 'direction': 'TB', 'parts': [], 'leaders': []})
        self.tree()
        self.apply({'op': 'move', 'items': [{'id': 'sense01', 'x': 12.34, 'y': -5}]},
                   {'op': 'collapse', 'id': 'sight01', 'collapsed': True}, {'op': 'rename', 'id': 'camera1', 'name': '  相机  标定 '},
                   {'op': 'direction', 'direction': 'LR'})
        state = domains.snapshot(self.conn, 'demo')
        self.assertEqual((state['version'], state['direction']), (2, 'LR'))
        parts = {part['id']: part for part in state['parts']}
        self.assertEqual((parts['sense01']['x'], parts['sense01']['y']), (12.3, -5.0))
        self.assertTrue(parts['sight01']['collapsed'])
        self.assertEqual(parts['camera1']['name'], '相机 标定')
        self.assertEqual(self.places(), {'vision': ('sight01', True), 'depth': ('sight01', False), 'cam': ('camera1', False)})
        sense, sight = {'id': 'sense01', 'name': '感知'}, {'id': 'sight01', 'name': '视觉'}
        self.assertEqual(domains.summary(self.conn, 'demo'), {'version': 2, 'paths': {
            'vision': [sense, sight], 'depth': [sense, sight], 'cam': [sense, sight, {'id': 'camera1', 'name': '相机 标定'}]}})
        self.assertEqual(domains.snapshot(self.conn, 'other')['parts'], [])
        # A direction change on a board nobody edited yet creates its record.
        self.assertEqual(self.apply({'op': 'direction', 'direction': 'LR'}, project='other')['board']['direction'], 'LR')

    def test_many_leaders_per_part_and_one_lead(self):
        self.tree()
        self.apply({'op': 'assign', 'session_id': 'voice', 'domain_id': 'sight01'})
        self.assertEqual(self.places()['voice'], ('sight01', False))
        effects = self.apply({'op': 'lead', 'session_id': 'depth', 'lead': True})['effects']
        self.assertEqual(effects, [{'op': 'lead', 'domain_id': 'sight01', 'previous': 'vision'}])
        self.assertEqual({sid: place for sid, place in self.places().items() if place[1]}, {'depth': ('sight01', True)})
        self.apply({'op': 'lead', 'session_id': 'depth', 'lead': False})
        self.assertFalse(any(lead for _, lead in self.places().values()))
        # Leading belongs to the part: moving elsewhere drops it, staying put keeps it.
        self.apply({'op': 'lead', 'session_id': 'vision', 'lead': True}, {'op': 'assign', 'session_id': 'vision', 'domain_id': 'sight01'})
        self.assertEqual(self.places()['vision'], ('sight01', True))
        self.apply({'op': 'assign', 'session_id': 'vision', 'domain_id': 'camera1'})
        self.assertEqual(self.places()['vision'], ('camera1', False))
        self.apply({'op': 'assign', 'session_id': 'vision', 'domain_id': None})
        self.assertNotIn('vision', self.places())
        with self.assertRaises(board.BoardError):
            self.apply({'op': 'lead', 'session_id': 'vision', 'lead': True})
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute('UPDATE domain_leaders SET lead=1 WHERE domain_id=?', ('sight01',))

    def test_deleting_a_part_moves_its_children_and_all_leaders_up_one_level(self):
        self.tree()
        self.apply(self.create('lens001', '镜头', 'camera1'))
        effects = self.apply({'op': 'delete', 'id': 'sight01'})['effects']
        self.assertEqual(effects, [{'op': 'delete', 'id': 'sight01', 'children': ['camera1'], 'parent_id': 'sense01',
                                    'leaders': ['depth', 'vision'], 'lead': 'vision'}])
        self.assertEqual(self.parts()['camera1']['parent_id'], 'sense01')
        self.assertEqual(self.places(), {'vision': ('sense01', False), 'depth': ('sense01', False), 'cam': ('camera1', False)})
        self.apply({'op': 'delete', 'id': 'sense01'})
        self.assertEqual(self.places(), {'cam': ('camera1', False)})
        self.assertIsNone(self.parts()['camera1']['parent_id'])

    def test_undo_batch_restores_a_deleted_part_with_the_same_id(self):
        self.tree()
        self.apply({'op': 'delete', 'id': 'sight01'})
        self.apply(self.create('sight01', '视觉', 'sense01', 0, 160), {'op': 'parent', 'id': 'camera1', 'parent_id': 'sight01'},
                   {'op': 'assign', 'session_id': 'depth', 'domain_id': 'sight01'},
                   {'op': 'assign', 'session_id': 'vision', 'domain_id': 'sight01'},
                   {'op': 'lead', 'session_id': 'vision', 'lead': True})
        self.assertEqual(self.parts()['camera1']['parent_id'], 'sight01')
        self.assertEqual(self.places(), {'vision': ('sight01', True), 'depth': ('sight01', False), 'cam': ('camera1', False)})

    def test_invalid_batches_change_nothing(self):
        self.tree()
        before = domains.snapshot(self.conn, 'demo')
        invalid = [
            [{'op': 'parent', 'id': 'sense01', 'parent_id': 'camera1'}],
            [{'op': 'parent', 'id': 'sense01', 'parent_id': 'sense01'}],
            [{'op': 'rename', 'id': 'sense01', 'name': '新名字'}, {'op': 'rename', 'id': 'sense01', 'name': '   '}],
            [{'op': 'rename', 'id': 'sense01', 'name': '长' * 41}],
            [{'op': 'assign', 'session_id': 'worker', 'domain_id': 'sense01'}],
            [{'op': 'assign', 'session_id': 'foreign', 'domain_id': 'sense01'}],
            [{'op': 'assign', 'session_id': 'missing', 'domain_id': 'sense01'}],
            [{'op': 'assign', 'session_id': 'voice', 'domain_id': 'missing'}],
            [{'op': 'lead', 'session_id': 'voice', 'lead': True}], [{'op': 'lead', 'session_id': 'cam', 'lead': 1}],
            [{'op': 'direction', 'direction': 'RL'}],
            [self.create('sense01', '重复')], [self.create('bad id!', 'x')],
            [{'op': 'move', 'items': [{'id': 'sense01', 'x': float('nan'), 'y': 0}]}],
            [{'op': 'move', 'items': [{'id': 'sense01', 'x': 2e6, 'y': 0}]}],
            [{'op': 'move', 'items': [{'id': 'sense01', 'x': True, 'y': 0}]}],
            [{'op': 'move', 'items': [{'id': 'cam', 'x': 0, 'y': 0}]}],
            [{'op': 'collapse', 'id': 'sense01', 'collapsed': 1}], [{'op': 'explode'}], ['create'], [],
        ]
        for ops in invalid:
            with self.subTest(ops=ops):
                with self.assertRaises(board.BoardError):
                    self.apply(*ops)
                self.assertEqual(domains.snapshot(self.conn, 'demo'), before)
        with self.assertRaises(board.BoardError):
            domains.apply(self.conn, 'demo', 'create', None, now=NOW)
        other = self.apply(self.create('far0001', '远方'), project='other')['board']
        with self.assertRaises(board.BoardError):
            self.apply({'op': 'parent', 'id': 'sense01', 'parent_id': 'far0001'})
        with self.assertRaises(board.BoardError):
            self.apply({'op': 'assign', 'session_id': 'foreign', 'domain_id': 'far0001'}, project='demo')
        self.assertEqual(other['version'], 1)
        self.assertEqual(domains.snapshot(self.conn, 'demo'), before)

    def test_stale_base_version_is_a_conflict(self):
        self.tree()
        with self.assertRaises(board.Conflict):
            self.apply({'op': 'rename', 'id': 'sense01', 'name': '旧页面'}, base=0)
        self.assertEqual(self.apply({'op': 'rename', 'id': 'sense01', 'name': '新页面'}, base=1)['board']['version'], 2)
        with self.assertRaises(board.BoardError):
            self.apply({'op': 'rename', 'id': 'sense01', 'name': 'x'}, base='2')

    def test_archived_leader_keeps_its_part_and_deleted_leader_leaves(self):
        self.tree()
        session_ops.archive(self.conn, 'vision', now=NOW)
        vision = next(item for item in domains.snapshot(self.conn, 'demo')['leaders'] if item['session_id'] == 'vision')
        self.assertEqual((vision['domain_id'], vision['lead'], vision['archived']), ('sight01', True, True))
        session_ops.restore(self.conn, 'vision', now=NOW)
        self.assertEqual(self.places()['vision'], ('sight01', True))
        session_ops.delete(self.conn, 'vision', now=NOW)
        self.assertNotIn('vision', self.places())
        self.apply({'op': 'lead', 'session_id': 'depth', 'lead': True})
        self.assertEqual(self.places()['depth'], ('sight01', True))


class DomainMigrationTests(unittest.TestCase):
    def test_first_version_board_upgrades_to_members_and_direction(self):
        temp = tempfile.TemporaryDirectory(prefix='crew-domains-old-')
        self.addCleanup(temp.cleanup)
        home = Path(temp.name)
        with closing(sqlite3.connect(home / 'crew.db')) as old:
            old.executescript('''
                CREATE TABLE domains (id TEXT PRIMARY KEY, project TEXT NOT NULL, parent_id TEXT, name TEXT NOT NULL,
                    x REAL NOT NULL DEFAULT 0, y REAL NOT NULL DEFAULT 0, collapsed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE domain_leaders (session_id TEXT PRIMARY KEY, project TEXT NOT NULL, domain_id TEXT,
                    x REAL, y REAL, updated_at TEXT NOT NULL);
                CREATE UNIQUE INDEX domain_leaders_part ON domain_leaders (domain_id) WHERE domain_id IS NOT NULL;
                CREATE TABLE domain_boards (project TEXT PRIMARY KEY, version INTEGER NOT NULL, updated_at TEXT NOT NULL);
                INSERT INTO domain_boards VALUES ('demo', 7, 'then');''')
        for _ in range(2):  # The upgrade is idempotent.
            with closing(store.connect(home)) as conn:
                (home / 'demo').mkdir(exist_ok=True)
                if conn.execute("SELECT 1 FROM projects WHERE name='demo'").fetchone() is None:
                    board.init_project(conn, 'demo', home / 'demo', now=NOW)
                    for sid in ('a', 'b'):
                        board.join(conn, sid, 'demo', sid, home / 'demo', home / 'demo', 'main', role='leader', now=NOW)
                    conn.execute("INSERT INTO domains VALUES ('part001','demo',NULL,'旧','0','0',0,'then','then')")
                    conn.execute("INSERT INTO domain_leaders(session_id,project,domain_id,updated_at) VALUES ('a','demo','part001','then')")
                self.assertEqual({row['name'] for row in conn.execute('PRAGMA index_list(domain_leaders)')} - {'sqlite_autoindex_domain_leaders_1'},
                                 {'domain_leaders_lead'})
                state = domains.snapshot(conn, 'demo')
                self.assertEqual((state['version'], state['direction']), (7, 'TB'))
                self.assertEqual([(item['session_id'], item['lead']) for item in state['leaders']], [('a', False)])
        with closing(store.connect(home)) as conn:
            domains.apply(conn, 'demo', [{'op': 'assign', 'session_id': 'b', 'domain_id': 'part001'},
                                         {'op': 'lead', 'session_id': 'b', 'lead': True}], now=NOW)
            self.assertEqual({(item['session_id'], item['lead']) for item in domains.snapshot(conn, 'demo')['leaders']},
                             {('a', False), ('b', True)})


class DomainHttpTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        poll = mock.patch.object(web.Service, '_poll_notices', return_value=None)
        poll.start()
        self.addCleanup(poll.stop)
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=lambda: [], claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=store.parse_iso(NOW), process_factory=self.process_factory,
            notification_client=mock.Mock(), lock_status=lambda: False,
            vscode_state_db=self.vscode_state_db)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.opener = build_opener(ProxyHandler({}))

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

    def request(self, path, data=None):
        req = Request(self.server.url + path, None if data is None else json.dumps(data).encode(), {'X-Crew': '1', 'X-Crew-Request-Id': uuid.uuid4().hex})
        try:
            response = self.opener.open(req, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_board_round_trip_paths_in_state_and_conflicts(self):
        code, body = self.request('/api/op/domains.get?project=demo')
        self.assertEqual((code, body['version'], body['direction'], body['parts']), (200, 0, 'TB', []))
        code, body = self.request('/api/op/domains.apply', {'project': 'demo', 'base_version': 0, 'ops': [
            {'op': 'create', 'id': 'sense01', 'name': '感知', 'parent_id': None, 'x': 0, 'y': 0},
            {'op': 'assign', 'session_id': 's3', 'domain_id': 'sense01'}, {'op': 'lead', 'session_id': 's3', 'lead': True}]})
        self.assertEqual(code, 200, body)
        self.assertEqual(body['board']['version'], 1)
        code, state = self.request('/api/op/workspace.state?project=demo')
        self.assertEqual(state['domains'], {'version': 1, 'paths': {'s3': [{'id': 'sense01', 'name': '感知'}]}})
        code, body = self.request('/api/op/domains.apply', {'project': 'demo', 'base_version': 0,
                                                   'ops': [{'op': 'rename', 'id': 'sense01', 'name': '旧'}]})
        self.assertEqual(code, 409)
        self.assertIn('别处改动', body['error'])
        self.assertEqual(self.request('/api/op/domains.apply?project=demo', {'project': 'other', 'ops': []})[0], 400)
        self.assertEqual(self.request('/api/op/domains.apply', {'project': 'demo', 'ops': [{'op': 'assign', 'session_id': 's1',
                                                                                    'domain_id': 'sense01'}]})[0], 400)
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(domains.leader_paths(conn, 'demo'), {'s3': [{'id': 'sense01', 'name': '感知'}]})

    def test_page_assets_and_remote_routes(self):
        self.assertTrue(routes.is_op_path('/api/op/domains.apply'))
        for kind in ('browser', 'desktop'):
            self.assertTrue(remote_routes.permitted('GET', '/api/op/domains.get', (), kind))
            self.assertTrue(remote_routes.permitted('POST', '/api/op/domains.apply', (), kind))
        for asset in ('/architecture.js', '/architecture.css'):
            with self.subTest(asset=asset):
                with self.opener.open(self.server.url + asset, timeout=5) as response:
                    self.assertEqual(response.status, 200)


if __name__ == '__main__':
    unittest.main()
