"""New AI jobs work with Codex alone; old identities and paid cards survive."""
import tests
from contextlib import closing
import json
import unittest
from unittest import mock

from crew import board, runtimes, spawn, store
import test_runtime_spawn as runtime_fixtures


class DefaultRuntimeTests(unittest.TestCase):
    def test_installed_carrier_selection_and_missing_tools(self):
        for installed, expected in (({'codex'}, 'codex'), ({'claude'}, 'claude')):
            with self.subTest(installed=installed):
                self.assertEqual(runtimes.default_runtime(lambda name: name if name in installed else None), expected)
        with self.assertRaisesRegex(board.BoardError, '安装并登录'):
            runtimes.default_runtime(lambda name: None)
        with self.assertRaisesRegex(board.BoardError, '设置页选择'):
            runtimes.default_runtime(lambda name: name)


class CodexOnlySpawnTests(runtime_fixtures.RuntimeFixture):
    plan = runtime_fixtures.RuntimeSpawnTests.plan
    run_cli = runtime_fixtures.RuntimeSpawnTests.run_cli

    def setUp(self):
        super().setUp()
        from crew import settings
        settings.save(self.conn, {'default': {'runtime': None, 'model': None, 'effort': None}})
        resolver = mock.patch.object(spawn.codex_client, 'command', return_value=runtime_fixtures.CODEX_COMMAND)
        resolver.start()
        self.addCleanup(resolver.stop)

    def test_new_leader_ungrouped_worker_and_cli_dry_run_choose_installed_codex(self):
        with mock.patch.object(runtimes.shutil, 'which', side_effect=lambda name: 'codex.exe' if name == 'codex' else None):
            for name, role in (('new-chief', 'leader'), ('ungrouped', 'member')):
                plan = spawn.prepare(self.conn, self.home, self.root, 'demo', name, 'work', role=role)
                self.assertEqual(plan['spec']['runtime'], 'codex')
                self.assertIsNone(plan['spec']['model'])
            code, output, errors = self.run_cli(['leader', 'start', '--name', 'cli-chief', '--dry-run'])
            self.assertEqual(code, 0, errors)
            self.assertEqual(json.loads(output)['spec']['runtime'], 'codex')

    def test_explicit_carrier_is_retained_and_unconfigured_member_prefers_codex(self):
        with mock.patch.object(runtimes, 'default_runtime', side_effect=AssertionError('Keep the chosen carrier')), \
                mock.patch('shutil.which', side_effect=lambda name: '/fixture/' + name if name in ('claude', 'codex') else None):
            self.assertEqual(self.plan(runtime='claude')['spec']['runtime'], 'claude')
            self.assertEqual(self.plan()['spec']['runtime'], 'codex')

    def test_status_reads_codex_host_without_invoking_claude_discovery(self):
        for person in board.sessions(self.conn, 'demo'):
            runtimes.set(self.conn, person['session_id'], 'codex')
        state = {'leaders': [{'session_id': 'chief-id', 'status': 'working', 'where': 'bg', 'context': None}],
                 'members': []}
        # run_cli's own helper supplies an empty state, so invoke the production
        # context merger against the host snapshot separately below.
        with mock.patch('crew.cli._agents', side_effect=AssertionError('No Claude discovery')):
            code, output, errors = self.run_cli(['status', '--json'])
        self.assertEqual(code, 0, errors)
        people = json.loads(output)['groups']
        self.assertTrue(any(p['runtime'] == 'codex' for g in people for p in g['members']))
        from crew import cli
        chief = {**board.session(self.conn, 'chief-id'), **runtimes.get(self.conn, 'chief-id')}
        with mock.patch.object(spawn, 'request_state', return_value=state):
            cli._with_context(self.home, 'demo', [chief])
        self.assertEqual((chief['status'], chief['where']), ('working', 'bg'))

    def test_existing_paid_digest_migrates_without_losing_its_identity(self):
        from crew import channel
        message = channel.post(self.conn, 'demo', board.HUMAN, 'release ready', source='web')
        self.conn.execute('DROP TABLE channel_digests')
        self.conn.execute('''CREATE TABLE channel_digests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL,
            upto_message_id INTEGER NOT NULL, body TEXT NOT NULL,
            message_count INTEGER NOT NULL, trigger TEXT NOT NULL,
            model TEXT NOT NULL, cost_usd REAL NOT NULL, created_at TEXT NOT NULL)''')
        self.conn.execute('INSERT INTO channel_digests VALUES(7,?,?,?,?,?,?,?,?)',
                          ('demo', message['id'], 'old paid card', 1, 'manual', 'haiku-original', .125, 'yesterday'))
        with closing(store.connect(self.home)) as migrated:
            card = channel.digest(migrated, 'demo')
            self.assertEqual((card['id'], card['body'], card['model'], card['cost_usd'], card['runtime']),
                             (7, 'old paid card', 'haiku-original', .125, 'claude'))
            migrated.execute('INSERT INTO channel_digests(project,upto_message_id,body,message_count,trigger,model,runtime,cost_usd,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                             ('demo', message['id'], 'new card', 1, 'manual', 'actual-codex', 'codex', None, 'today'))
            latest = channel.digest(migrated, 'demo')
            self.assertEqual(latest['id'], 8)
            self.assertIsNone(latest['cost_usd'])
            self.assertEqual(migrated.execute('PRAGMA foreign_key_check').fetchall(), [])


if __name__ == '__main__':
    unittest.main()
