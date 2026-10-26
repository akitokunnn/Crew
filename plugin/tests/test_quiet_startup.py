"""Ordinary startup must not turn viewing workspaces into model work."""
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import crew_desktop
from crew import board, runtimes, web
from tests.test_runtimes import RuntimeFixture


class QuietStartupTests(RuntimeFixture):
    def test_real_serve_entry_does_not_queue_inventory_for_any_workspace(self):
        other = self.root / 'other'
        other.mkdir()
        board.init_project(self.conn, 'other', other)
        board.join(self.conn, 'codex-chief', 'other', 'codex-chief', other, other,
                   'main', role='leader')
        runtimes.set(self.conn, 'codex-chief', 'codex')
        factory = mock.Mock(side_effect=AssertionError('No real carrier may start'))
        options = dict(agents_provider=lambda: [], claude_root=self.root / 'claude',
                       codex_root=self.root / 'codex', vscode_state_db=self.root / 'vscode',
                       cwd=self.root, process_factory=factory)
        # Exercise Server -> Service.start, but prevent unrelated background work.
        with mock.patch.object(web.Service, '_poll_agents'), \
                mock.patch.object(web.Service, '_poll_notices'), \
                mock.patch.object(web.Service, '_poll_git_ci'), \
                mock.patch.object(web.connections.Manager, 'start'), \
                mock.patch.object(web.Server, 'serve_forever', side_effect=KeyboardInterrupt), \
                contextlib.redirect_stdout(io.StringIO()):
            for _ in range(2):
                web._serve_locked(self.home, Path(web.__file__).parents[1], 0, False, **options)
        factory.assert_not_called()
        for sid in ('chief-id', 'codex-chief'):
            self.assertEqual(board.pending_notices(self.conn, sid), [], sid)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM todo_backfills').fetchone()[0], 0)

    def test_desktop_entry_keeps_existing_human_notice_without_adding_inventory(self):
        from crew import todo
        item = todo.add(self.conn, 'member-id', '授权', '打开设置', '看到授权成功')
        todo.resolve(self.conn, 'demo', item['id'], 'done', note='已授权')
        before = board.pending_notices(self.conn, 'chief-id')
        factory = mock.Mock(side_effect=AssertionError('No real carrier may start'))
        create_server = web.create_server

        def isolated_server(home, plugin_root, port, **options):
            return create_server(home, plugin_root, port, process_factory=factory, **options)

        with mock.patch.object(crew_desktop.install, 'install'), \
                mock.patch.object(web, 'create_server', side_effect=isolated_server), \
                mock.patch.object(web.Service, '_poll_agents'), \
                mock.patch.object(web.Service, '_poll_notices'), \
                mock.patch.object(web.Service, '_poll_git_ci'), \
                mock.patch.object(web.connections.Manager, 'start'), \
                mock.patch.object(web.Server, 'serve_forever'), \
                mock.patch.object(web.Server, 'shutdown'), \
                mock.patch.object(crew_desktop.sys, 'stdin', io.StringIO('shutdown\n')), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(crew_desktop.serve(SimpleNamespace(isolated_sources=True, port=0), self.home), 0)
        factory.assert_not_called()
        self.assertEqual(board.pending_notices(self.conn, 'chief-id'), before)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM todo_backfills').fetchone()[0], 0)

