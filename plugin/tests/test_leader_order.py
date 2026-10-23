"""Project-scoped leader order through an isolated HTTP service."""
import tests
from contextlib import closing
import json
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from crew import board, session_ops, store, web
from test_shell import NOW, PLUGIN, ShellFixture


class LeaderOrderTests(ShellFixture, unittest.TestCase):
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
        self.add_leader('second', 'second')

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

    def add_leader(self, sid, name, project='demo'):
        board.join(self.conn, sid, project, name, self.project, self.project, 'main', role='leader', now=NOW)

    def request(self, path, data=None):
        req = Request(self.server.url + path, None if data is None else json.dumps(data).encode(), {'X-Crew': '1'})
        try:
            response = self.opener.open(req, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def order(self, order, project='demo'):
        return self.request('/api/leaders/order', {'project': project, 'order': order})

    def ids(self, project='demo'):
        code, state = self.request('/api/state?project=' + project)
        self.assertEqual(code, 200, state)
        return [s['session_id'] for s in state['leaders']]

    def test_order_success_persists_and_state_uses_saved_positions(self):
        self.assertEqual(self.ids(), ['s3', 'second'])
        self.assertEqual(self.order(['second', 's3']), (200, {'ok': True}))
        self.assertEqual(self.ids(), ['second', 's3'])
        with closing(store.connect(self.home)) as conn:
            self.assertEqual([tuple(r) for r in conn.execute(
                'SELECT session_id,position FROM leader_order WHERE project=? ORDER BY position', ('demo',))],
                [('second', 0), ('s3', 1)])

    def test_foreign_member_and_unknown_ids_are_rejected_without_changing_order(self):
        board.init_project(self.conn, 'other', self.root / 'other', now=NOW)
        self.add_leader('foreign', 'foreign', 'other')
        self.order(['second', 's3'])
        for invalid in ('foreign', 's1', 'missing'):
            with self.subTest(invalid=invalid):
                code, body = self.order(['second', 's3', invalid])
                self.assertEqual(code, 400)
                self.assertIn('不属于该项目', body['error'])
                self.assertEqual(self.ids(), ['second', 's3'])

    def test_missing_leader_is_rejected(self):
        code, body = self.order(['second'])
        self.assertEqual(code, 400)
        self.assertIn('缺少当前显示的 leader：s3', body['error'])
        self.assertEqual(self.ids(), ['s3', 'second'])

    def test_duplicate_invalid_shape_and_project_mismatch_are_rejected(self):
        for order in (['s3', 'second', 's3'], 's3', [1], None):
            with self.subTest(order=order):
                self.assertEqual(self.order(order)[0], 400)
        self.assertEqual(self.request('/api/leaders/order?project=demo', {'project': 'other', 'order': []})[0], 400)
        self.assertEqual(self.request('/api/leaders/order', {'order': ['s3', 'second']})[0], 400)

    def test_new_leaders_follow_saved_order_in_existing_relative_order_and_projects_are_isolated(self):
        self.order(['second', 's3'])
        self.add_leader('new-z', 'z')
        self.add_leader('new-a', 'a')
        self.assertEqual(self.ids(), ['second', 's3', 'new-a', 'new-z'])
        board.init_project(self.conn, 'other', self.root / 'other', now=NOW)
        self.add_leader('other-a', 'a', 'other')
        self.add_leader('other-b', 'b', 'other')
        self.assertEqual(self.order(['other-b', 'other-a'], 'other')[0], 200)
        self.assertEqual(self.ids('other'), ['other-b', 'other-a'])
        self.assertEqual(self.ids(), ['second', 's3', 'new-a', 'new-z'])

    def test_archived_and_removed_leaders_are_not_required_and_cannot_be_ordered(self):
        self.order(['second', 's3'])
        session_ops.archive(self.conn, 'second', now=NOW)
        self.add_leader('removed', 'removed')
        session_ops.archive(self.conn, 'removed', removed=True, now=NOW)
        self.assertEqual(self.ids(), ['s3'])
        self.assertEqual(self.order(['s3'])[0], 200)
        for sid in ('second', 'removed'):
            self.assertEqual(self.order(['s3', sid])[0], 400)
