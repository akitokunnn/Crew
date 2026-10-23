"""Project palette persistence and HTTP changes use temporary homes only."""
import tests  # Install the production-service connection guard.
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from crew import board, store, themes, web
from test_shell import NOW, PLUGIN, ShellFixture


class ProjectThemeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=PLUGIN, prefix='theme-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.conn = store.connect(self.root / 'home')
        self.addCleanup(self.conn.close)

    def add(self, name, now=NOW):
        return board.init_project(self.conn, name, self.root / name, now=now)

    def legacy(self, name, now=NOW):
        self.conn.execute('INSERT INTO projects VALUES(?,?,?)', (name, store.norm_path(self.root / name), now))

    def test_new_projects_use_every_palette_before_round_robin(self):
        assigned = [self.add('project-%02d' % index)['theme'] for index in range(14)]
        self.assertEqual(assigned, list(themes.IDS) * 2 + list(themes.IDS[:2]))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM project_themes').fetchone()[0], 14)

    def test_manual_choice_frees_palette_for_next_project(self):
        self.add('first')
        board.set_project_theme(self.conn, 'first', 'indigo', now=NOW)
        self.assertEqual(self.add('second')['theme'], 'celadon')
        self.assertEqual(self.add('third')['theme'], 'wisteria')

    def test_first_list_read_assigns_legacy_projects_by_enable_order_and_persists(self):
        self.legacy('alpha', now='2026-10-23T08:00:00.000Z')
        self.legacy('zeta', now='2026-10-21T08:00:00.000Z')
        self.legacy('beta', now='2026-10-21T08:00:00.000Z')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM project_themes').fetchone()[0], 0)
        self.assertEqual({p['name']: p['theme'] for p in board.projects(self.conn)},
                         {'zeta': 'celadon', 'beta': 'indigo', 'alpha': 'wisteria'})
        before = [tuple(row) for row in self.conn.execute('SELECT * FROM project_themes ORDER BY project')]
        board.projects(self.conn)
        self.assertEqual(before, [tuple(row) for row in self.conn.execute('SELECT * FROM project_themes ORDER BY project')])

    def test_single_project_read_assigns_all_legacy_projects_before_new_project(self):
        self.legacy('first')
        self.legacy('second')
        self.assertEqual(board.project(self.conn, 'second')['theme'], 'indigo')
        self.assertEqual(self.add('third')['theme'], 'wisteria')
        self.assertEqual(board.project(self.conn, 'first')['theme'], 'celadon')

    def test_new_project_allocates_legacy_rows_first(self):
        self.legacy('old')
        self.assertEqual(self.add('new')['theme'], 'indigo')
        self.assertEqual(board.project(self.conn, 'old')['theme'], 'celadon')

    def test_initialization_keeps_existing_choice(self):
        self.add('demo')
        board.set_project_theme(self.conn, 'demo', 'lotus', now=NOW)
        self.assertEqual(self.add('demo')['theme'], 'lotus')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM project_themes').fetchone()[0], 1)

    def test_assignment_participates_in_outer_transaction(self):
        self.conn.execute('BEGIN IMMEDIATE')
        self.legacy('temporary')
        self.assertEqual(board.project(self.conn, 'temporary')['theme'], 'celadon')
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM project_themes').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM projects').fetchone()[0], 0)

    def test_two_connections_assign_legacy_projects_once(self):
        for index in range(8):
            self.legacy('project-%02d' % index)
        start = threading.Barrier(2)

        def read():
            with closing(store.connect(self.root / 'home')) as conn:
                start.wait(timeout=5)
                return [p['theme'] for p in board.projects(conn)]

        with ThreadPoolExecutor(max_workers=2) as executor:
            first, second = executor.submit(read), executor.submit(read)
            expected = list(themes.IDS) + list(themes.IDS[:2])
            self.assertEqual(first.result(timeout=10), expected)
            self.assertEqual(second.result(timeout=10), expected)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM project_themes').fetchone()[0], 8)

    def test_invalid_choice_does_not_change_saved_theme(self):
        self.add('demo')
        for value in ('unknown', '', None):
            with self.assertRaisesRegex(board.BoardError, '主题不存在'):
                board.set_project_theme(self.conn, 'demo', value, now=NOW)
        self.assertEqual(board.project(self.conn, 'demo')['theme'], 'celadon')
        with self.assertRaises(board.BoardError):
            board.set_project_theme(self.conn, 'unknown', 'indigo', now=NOW)

    def test_new_table_does_not_extend_projects(self):
        self.assertEqual([r['name'] for r in self.conn.execute('PRAGMA table_info(projects)')],
                         ['name', 'root', 'created_at'])
        self.assertEqual([r['name'] for r in self.conn.execute('PRAGMA table_info(project_themes)')],
                         ['project', 'theme', 'assigned_at'])


class ProjectThemeHttpTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.server = web.create_server(
            self.home, PLUGIN, port=0, cwd=self.project, agents_provider=lambda: [],
            claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
            now=lambda: store.parse_iso(NOW), process_factory=self.process_factory,
            vscode_state_db=self.vscode_state_db, lock_status=lambda: False)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
        self.thread.start()
        self.addCleanup(self.close_server)
        self.assertTrue(self.server.service.agents_ready.wait(5))
        self.opener = build_opener(ProxyHandler({}))

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.assertFalse(self.thread.is_alive())

    def request(self, path, data=None, header=True):
        payload = None if data is None else json.dumps(data).encode('utf-8')
        headers = {'X-Crew': '1'} if header else {}
        try:
            response = self.opener.open(Request(self.server.url + path, payload, headers), timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def test_state_exposes_catalogue_and_each_project_choice(self):
        board.init_project(self.conn, 'other', self.root / 'other', now=NOW)
        code, landing = self.request('/api/state')
        self.assertEqual(code, 200)
        self.assertIsNone(landing['project'])
        self.assertEqual(landing['themes'], themes.THEMES)
        self.assertEqual(landing['projects'], [{'name': 'demo', 'theme': 'celadon'}, {'name': 'other', 'theme': 'indigo'}])
        code, selected = self.request('/api/state?project=other')
        self.assertEqual(code, 200)
        self.assertEqual(selected['project']['theme'], 'indigo')

    def test_change_is_immediate_persisted_and_project_scoped(self):
        board.init_project(self.conn, 'other', self.root / 'other', now=NOW)
        self.assertEqual(self.request('/api/project/theme', {'project': 'other', 'theme': 'lotus'}),
                         (200, {'ok': True, 'theme': 'lotus'}))
        self.assertEqual(self.request('/api/state?project=other')[1]['project']['theme'], 'lotus')
        self.assertEqual(board.project(self.conn, 'other')['theme'], 'lotus')
        self.assertEqual(board.project(self.conn, 'demo')['theme'], 'celadon')
        self.assertEqual(self.request('/api/project/theme?project=demo', {'theme': 'mist'}),
                         (200, {'ok': True, 'theme': 'mist'}))

    def test_invalid_theme_and_mismatched_project_are_rejected_without_change(self):
        for data in ({'theme': 'unknown'}, {'theme': ''}, {'theme': 42}, {'theme': None}):
            code, error = self.request('/api/project/theme?project=demo', data)
            self.assertEqual(code, 400, error)
        self.assertEqual(self.request('/api/project/theme?project=demo', {'project': 'other', 'theme': 'mist'})[0], 400)
        self.assertEqual(self.request('/api/project/theme', {'project': 'missing', 'theme': 'mist'})[0], 400)
        self.assertEqual(self.request('/api/project/theme?project=demo', {'theme': 'mist'}, header=False)[0], 403)
        self.assertEqual(board.project(self.conn, 'demo')['theme'], 'celadon')
