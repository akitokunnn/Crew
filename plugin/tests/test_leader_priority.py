"""Priority display uses isolated HTTP, temporary homes and fake processes."""
import tests
from contextlib import closing
import json
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from crew import board, session_ops, store, themes, web
from test_shell import NOW, PLUGIN, ShellFixture


class LeaderPriorityTests(ShellFixture, unittest.TestCase):
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

    def priority(self, ranks, project='demo'):
        return self.request('/api/leaders/priority', {'project': project, 'ranks': ranks})

    def priorities(self, project='demo'):
        code, state = self.request('/api/state?project=' + project)
        self.assertEqual(code, 200, state)
        return {s['session_id']: s['priority'] for s in state['leaders']}

    def test_complete_order_persists_and_empty_clears(self):
        self.add_leader('third', 'third')
        self.assertEqual(self.priority(['third', 's3', 'second']), (200, {'ok': True}))
        self.assertEqual(self.priorities(), {'s3': 2, 'second': 3, 'third': 1})
        with closing(store.connect(self.home)) as conn:
            self.assertEqual([tuple(r) for r in conn.execute(
                'SELECT session_id,position FROM leader_priority WHERE project=? ORDER BY position', ('demo',))],
                [('third', 0), ('s3', 1), ('second', 2)])
        self.assertEqual(self.priority([])[0], 200)
        self.assertEqual(set(self.priorities().values()), {None})

    def test_foreign_member_unknown_and_duplicate_are_rejected_atomically(self):
        board.init_project(self.conn, 'other', self.root / 'other', now=NOW)
        self.add_leader('foreign', 'foreign', 'other')
        self.priority(['second', 's3'])
        for ranks in (['foreign'], ['s1'], ['missing'], ['s3', 's3']):
            with self.subTest(ranks=ranks):
                self.assertEqual(self.priority(ranks)[0], 400)
                self.assertEqual(self.priorities(), {'s3': 2, 'second': 1})

    def test_more_than_three_and_invalid_shapes_are_rejected(self):
        self.add_leader('third', 'third')
        self.add_leader('fourth', 'fourth')
        for ranks in (['s3', 'second', 'third', 'fourth'], None, 's3', [1], {}):
            with self.subTest(ranks=ranks):
                self.assertEqual(self.priority(ranks)[0], 400)
        self.assertEqual(set(self.priorities().values()), {None})

    def test_project_required_mismatch_and_isolation(self):
        self.assertEqual(self.request('/api/leaders/priority', {'ranks': []})[0], 400)
        self.assertEqual(self.request('/api/leaders/priority?project=demo', {'project': 'other', 'ranks': []})[0], 400)
        board.init_project(self.conn, 'other', self.root / 'other', now=NOW)
        self.add_leader('foreign', 'foreign', 'other')
        self.assertEqual(self.priority(['foreign'], 'other')[0], 200)
        self.assertEqual(self.priority(['second'])[0], 200)
        self.assertEqual(self.priorities('other'), {'foreign': 1})
        self.assertEqual(self.priorities(), {'s3': None, 'second': 1})

    def test_archived_and_removed_compact_then_undo_restores(self):
        self.add_leader('third', 'third')
        self.priority(['s3', 'second', 'third'])
        for removed in (False, True):
            with self.subTest(removed=removed):
                result = session_ops.archive(self.conn, 's3', removed=removed, now=NOW)
                self.assertEqual(self.priorities(), {'second': 1, 'third': 2})
                self.assertEqual(self.priority(['s3'])[0], 400)
                session_ops.undo(self.conn, 's3', result['before'], now=NOW)
                self.assertEqual(self.priorities(), {'s3': 1, 'second': 2, 'third': 3})

    def test_ended_group_compacts_then_group_undo_restores(self):
        self.add_leader('third', 'third')
        self.priority(['s3', 'second', 'third'])
        result = session_ops.close_group(self.conn, 'demo', 'lead', now=NOW)
        self.assertEqual(self.priorities(), {'second': 1, 'third': 2})
        self.assertEqual(self.priority(['s3'])[0], 400)
        session_ops.undo_group(self.conn, 'demo', 'lead', result['before'], now=NOW)
        self.assertEqual(self.priorities(), {'s3': 1, 'second': 2, 'third': 3})

    def test_priority_not_exposed_to_board_status_for_leaders(self):
        self.priority(['s3'])
        status = board.status(self.conn, 'demo', [])
        self.assertTrue(all('priority' not in s for g in status['groups'] for s in g['members']))

    def test_theme_tokens_match_approved_palette_for_both_modes(self):
        for theme in themes.THEMES:
            for mode in ('light', 'dark'):
                with self.subTest(theme=theme['id'], mode=mode):
                    self.assertEqual([theme[mode]['--p' + str(rank)] for rank in (1, 2, 3)],
                        ['#008F88' if theme['id'] == 'mist' else '#008FA8',
                         '#687700' if theme['id'] == 'lotus' else '#B52B96',
                         '#687700' if theme['id'] == 'indigo' else '#5555DC'])
