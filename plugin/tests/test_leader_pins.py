"""Leader pins use isolated HTTP, temporary homes and fake processes."""
import tests
from contextlib import closing
import uuid
import json
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from crew import board, session_ops, store, web
from test_shell import NOW, PLUGIN, ShellFixture


class LeaderPinTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        poll = mock.patch.object(web.Service, '_poll_notices', return_value=None)
        poll.start()
        self.addCleanup(poll.stop)
        self.server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project,
            agents_provider=lambda: [], claude_root=self.claude, codex_root=self.codex,
            runs_file=self.runs, now=store.parse_iso(NOW), process_factory=self.process_factory,
            notification_client=mock.Mock(), idle_status=lambda: 0,
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
        req = Request(self.server.url + path, None if data is None else json.dumps(data).encode(), {'X-Crew': '1', 'X-Crew-Request-Id': uuid.uuid4().hex})
        try:
            response = self.opener.open(req, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def save(self, order, pinned=None, project='demo'):
        data = {'project': project, 'order': order}
        if pinned is not None:
            data['pinned'] = pinned
        return self.request('/api/op/leaders.order.set', data)

    def leaders(self, project='demo'):
        code, state = self.request('/api/op/workspace.state?project=' + project)
        self.assertEqual(code, 200, state)
        return [(s['session_id'], s['pinned']) for s in state['leaders']]

    def test_pinned_leaders_show_first_in_saved_order_and_empty_clears(self):
        self.add_leader('third', 'third')
        self.assertEqual(self.save(['s3', 'second', 'third'], ['third']), (200, {'ok': True}))
        self.assertEqual(self.leaders(), [('third', True), ('s3', False), ('second', False)])
        self.assertEqual(self.save(['second', 's3', 'third'], ['third', 's3'])[0], 200)
        self.assertEqual(self.leaders(), [('s3', True), ('third', True), ('second', False)])
        # Reordering without `pinned` keeps the pins.
        self.assertEqual(self.save(['third', 's3', 'second'])[0], 200)
        self.assertEqual(self.leaders(), [('third', True), ('s3', True), ('second', False)])
        self.assertEqual(self.save(['third', 's3', 'second'], [])[0], 200)
        self.assertEqual(self.leaders(), [('third', False), ('s3', False), ('second', False)])

    def test_foreign_unknown_and_invalid_pins_are_rejected_atomically(self):
        board.init_project(self.conn, 'other', self.root / 'other', now=NOW)
        self.add_leader('foreign', 'foreign', 'other')
        self.save(['second', 's3'], ['second'])
        for pinned in (['foreign'], ['s1'], ['missing'], 's3', [1], {}):
            with self.subTest(pinned=pinned):
                self.assertEqual(self.save(['s3', 'second'], pinned)[0], 400)
                self.assertEqual(self.leaders(), [('second', True), ('s3', False)])

    def test_archived_leader_keeps_its_pin_through_undo(self):
        self.add_leader('third', 'third')
        self.save(['s3', 'second', 'third'], ['s3'])
        result = session_ops.archive(self.conn, 's3', now=NOW)
        self.assertEqual(self.leaders(), [('second', False), ('third', False)])
        self.assertEqual(self.save(['third', 'second'], ['third'])[0], 200)
        session_ops.undo(self.conn, 's3', result['before'], now=NOW)
        self.assertEqual(self.leaders(), [('third', True), ('s3', True), ('second', False)])

    def test_former_priorities_become_pins_once(self):
        self.add_leader('third', 'third')
        with closing(store.connect(self.home)) as conn:
            conn.execute('CREATE TABLE leader_priority (project TEXT, session_id TEXT, position INTEGER)')
            conn.execute("INSERT INTO leader_priority VALUES ('demo','third',0),('demo','s3',1)")
        with closing(store.connect(self.home)) as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='leader_priority'").fetchone())
        self.assertEqual([sid for sid, pinned in self.leaders() if pinned], ['s3', 'third'])
