"""Architecture board domain tree: isolated homes, temporary projects and an isolated HTTP service."""
import tests  # Install the production-service connection guard.
from contextlib import closing
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

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
        for sid, name in (('cam', '相机'), ('vision', '视觉'), ('voice', '声纹')):
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
        """Builds 感知 › 视觉 › 相机 with a leader on 视觉 and one on 相机."""
        self.apply(self.create('sense01', '感知'), self.create('sight01', '视觉', 'sense01', 300, 0),
                   self.create('camera1', '相机', 'sight01', 600, 0),
                   {'op': 'assign', 'session_id': 'vision', 'domain_id': 'sight01'},
                   {'op': 'assign', 'session_id': 'cam', 'domain_id': 'camera1'})

    def parts(self):
        return {part['id']: part for part in domains.snapshot(self.conn, 'demo')['parts']}

    def places(self):
        return {item['session_id']: item['domain_id'] for item in domains.snapshot(self.conn, 'demo')['leaders']}

    def test_tree_paths_layout_and_version(self):
        self.assertEqual(domains.snapshot(self.conn, 'demo'), {'project': 'demo', 'version': 0, 'parts': [], 'leaders': []})
        self.tree()
        self.apply({'op': 'move', 'items': [{'kind': 'part', 'id': 'sense01', 'x': 12.34, 'y': -5},
                                            {'kind': 'leader', 'id': 'voice', 'x': 40, 'y': 80}]},
                   {'op': 'collapse', 'id': 'sight01', 'collapsed': True}, {'op': 'rename', 'id': 'camera1', 'name': '  相机  标定 '})
        board_state = domains.snapshot(self.conn, 'demo')
        self.assertEqual(board_state['version'], 2)
        parts = {part['id']: part for part in board_state['parts']}
        self.assertEqual((parts['sense01']['x'], parts['sense01']['y']), (12.3, -5.0))
        self.assertTrue(parts['sight01']['collapsed'])
        self.assertEqual(parts['camera1']['name'], '相机 标定')
        leaders = {item['session_id']: item for item in board_state['leaders']}
        self.assertEqual((leaders['voice']['domain_id'], leaders['voice']['x'], leaders['voice']['y']), (None, 40.0, 80.0))
        self.assertEqual(leaders['cam']['name'], '相机')
        self.assertEqual(domains.summary(self.conn, 'demo'), {'version': 2, 'paths': {
            'vision': [{'id': 'sense01', 'name': '感知'}, {'id': 'sight01', 'name': '视觉'}],
            'cam': [{'id': 'sense01', 'name': '感知'}, {'id': 'sight01', 'name': '视觉'}, {'id': 'camera1', 'name': '相机 标定'}]}})
        self.assertEqual(domains.snapshot(self.conn, 'other')['parts'], [])

    def test_assigning_moves_a_leader_and_replaces_the_previous_one_like_a_single_pin(self):
        self.tree()
        self.apply({'op': 'move', 'items': [{'kind': 'leader', 'id': 'voice', 'x': 7, 'y': 8}]})
        result = self.apply({'op': 'assign', 'session_id': 'voice', 'domain_id': 'sight01'})
        self.assertEqual(result['effects'], [{'op': 'displaced', 'session_id': 'vision', 'domain_id': 'sight01'}])
        self.assertEqual(self.places(), {'cam': 'camera1', 'vision': None, 'voice': 'sight01'})
        self.assertEqual(self.apply({'op': 'rename', 'id': 'sight01', 'name': '视觉'}, self.create('hear001', '听觉'),
                                    {'op': 'assign', 'session_id': 'vision', 'domain_id': 'hear001'})['effects'],
                         [None, {'op': 'create', 'id': 'hear001'}, None])
        voice = next(item for item in result['board']['leaders'] if item['session_id'] == 'voice')
        self.assertEqual((voice['x'], voice['y']), (7.0, 8.0))
        self.apply({'op': 'assign', 'session_id': 'cam', 'domain_id': 'sense01'})
        self.assertEqual(self.places(), {'cam': 'sense01', 'vision': 'hear001', 'voice': 'sight01'})
        self.apply({'op': 'assign', 'session_id': 'cam', 'domain_id': None})
        self.assertEqual(self.places()['cam'], None)

    def test_deleting_a_part_moves_its_children_and_leader_up_one_level(self):
        self.tree()
        self.apply(self.create('lens001', '镜头', 'camera1'))
        effects = self.apply({'op': 'delete', 'id': 'camera1'})['effects']
        self.assertEqual(effects, [{'op': 'delete', 'id': 'camera1', 'children': ['lens001'], 'parent_id': 'sight01',
                                    'leader': 'cam', 'leader_to': None}])
        self.assertEqual(self.parts()['lens001']['parent_id'], 'sight01')
        self.assertEqual(self.places(), {'cam': None, 'vision': 'sight01'})
        # A free parent takes the leader; a top-level part's leader becomes unassigned.
        self.apply({'op': 'assign', 'session_id': 'cam', 'domain_id': 'lens001'}, {'op': 'delete', 'id': 'sight01'})
        self.assertEqual(self.places(), {'cam': 'lens001', 'vision': 'sense01'})
        self.assertEqual(self.parts()['lens001']['parent_id'], 'sense01')
        self.apply({'op': 'delete', 'id': 'sense01'})
        self.assertEqual(self.places(), {'cam': 'lens001', 'vision': None})
        self.assertEqual(set(self.parts()), {'lens001'})
        self.assertIsNone(self.parts()['lens001']['parent_id'])

    def test_undo_batch_restores_a_deleted_part_with_the_same_id(self):
        self.tree()
        self.apply({'op': 'delete', 'id': 'sight01'})
        self.apply(self.create('sight01', '视觉', 'sense01', 300, 0), {'op': 'parent', 'id': 'camera1', 'parent_id': 'sight01'},
                   {'op': 'assign', 'session_id': 'vision', 'domain_id': 'sight01'})
        self.assertEqual(self.parts()['camera1']['parent_id'], 'sight01')
        self.assertEqual(self.places(), {'cam': 'camera1', 'vision': 'sight01'})

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
            [self.create('sense01', '重复')], [self.create('bad id!', 'x')],
            [{'op': 'move', 'items': [{'kind': 'part', 'id': 'sense01', 'x': float('nan'), 'y': 0}]}],
            [{'op': 'move', 'items': [{'kind': 'part', 'id': 'sense01', 'x': 2e6, 'y': 0}]}],
            [{'op': 'move', 'items': [{'kind': 'part', 'id': 'sense01', 'x': True, 'y': 0}]}],
            [{'op': 'move', 'items': [{'kind': 'group', 'id': 'sense01', 'x': 0, 'y': 0}]}],
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

    def test_archived_leader_keeps_its_part_and_deleted_leader_frees_it(self):
        self.tree()
        session_ops.archive(self.conn, 'cam', now=NOW)
        cam = next(item for item in domains.snapshot(self.conn, 'demo')['leaders'] if item['session_id'] == 'cam')
        self.assertEqual((cam['domain_id'], cam['archived']), ('camera1', True))
        session_ops.restore(self.conn, 'cam', now=NOW)
        self.assertEqual(self.places()['cam'], 'camera1')
        session_ops.delete(self.conn, 'cam', now=NOW)
        self.assertNotIn('cam', self.places())
        self.apply({'op': 'assign', 'session_id': 'voice', 'domain_id': 'camera1'})
        self.assertEqual(self.places()['voice'], 'camera1')


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
        req = Request(self.server.url + path, None if data is None else json.dumps(data).encode(), {'X-Crew': '1'})
        try:
            response = self.opener.open(req, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_board_round_trip_paths_in_state_and_conflicts(self):
        code, body = self.request('/api/domains?project=demo')
        self.assertEqual((code, body['version'], body['parts']), (200, 0, []))
        code, body = self.request('/api/domains', {'project': 'demo', 'base_version': 0, 'ops': [
            {'op': 'create', 'id': 'sense01', 'name': '感知', 'parent_id': None, 'x': 0, 'y': 0},
            {'op': 'assign', 'session_id': 's3', 'domain_id': 'sense01'}]})
        self.assertEqual(code, 200, body)
        self.assertEqual(body['board']['version'], 1)
        code, state = self.request('/api/state?project=demo')
        self.assertEqual(state['domains'], {'version': 1, 'paths': {'s3': [{'id': 'sense01', 'name': '感知'}]}})
        code, body = self.request('/api/domains', {'project': 'demo', 'base_version': 0,
                                                   'ops': [{'op': 'rename', 'id': 'sense01', 'name': '旧'}]})
        self.assertEqual(code, 409)
        self.assertIn('别处改动', body['error'])
        self.assertEqual(self.request('/api/domains?project=demo', {'project': 'other', 'ops': []})[0], 400)
        self.assertEqual(self.request('/api/domains', {'project': 'demo', 'ops': [{'op': 'assign', 'session_id': 's1',
                                                                                    'domain_id': 'sense01'}]})[0], 400)
        with closing(store.connect(self.home)) as conn:
            self.assertEqual(domains.leader_paths(conn, 'demo'), {'s3': [{'id': 'sense01', 'name': '感知'}]})

    def test_page_assets_and_remote_routes(self):
        self.assertIn('/api/domains', web.POST_PATHS)
        self.assertTrue(remote_routes.permitted('GET', '/api/domains', (), 'browser'))
        self.assertTrue(remote_routes.permitted('POST', '/api/domains', (), 'browser'))
        for asset in ('/architecture.js', '/architecture.css'):
            with self.subTest(asset=asset):
                with self.opener.open(self.server.url + asset, timeout=5) as response:
                    self.assertEqual(response.status, 200)


if __name__ == '__main__':
    unittest.main()
