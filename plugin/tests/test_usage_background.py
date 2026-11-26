"""Usage scanning runs in the background; the workspace snapshot only reads (docs/ARCHITECTURE_REVIEW.md stage 2)."""
import tests
import json
import re
import threading
import unittest
from unittest import mock

from crew import change_feed, claude_budget, store, usage, web
from crew.web_service import usage as usage_steps
from test_shell import NOW, PLUGIN, ShellFixture

WRITES = re.compile(r'\s*(INSERT|UPDATE|DELETE|REPLACE|BEGIN|SAVEPOINT)\b', re.I)


class UsageBackgroundTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.clock = store.parse_iso(NOW)
        self.service = web.Service(self.home, PLUGIN, agents_provider=lambda: [], claude_root=self.claude,
                                   codex_root=self.codex, runs_file=self.runs, process_factory=self.process_factory,
                                   isolated_sources=True, now=lambda: self.clock)
        self.addCleanup(self.service.chat.close)

    def limits_changes(self, after):
        """The limits values the scan recorded, one row per recording (a reader would merge them into the last)."""
        return [{'seq': row['seq'], 'data': json.loads(row['data'])} for row in self.conn.execute(
            "SELECT seq,data FROM change_log WHERE seq>? AND type='limits.updated' ORDER BY seq", (after,))]

    def page_fields(self, state):
        return {key: json.loads(json.dumps(state[key])) for key in ('limits', 'usage_scanned_at', 'usage_error')}

    def test_the_snapshot_neither_scans_nor_writes_nor_waits_for_the_writer(self):
        self.service.scan_usage()
        self.write(self.runs, [self.run_record('late-receipt', cwd=str(self.project))])
        statements, original = [], store.connect

        def connect(home):
            conn = original(home)
            conn.set_trace_callback(lambda sql: statements.append(sql) if WRITES.match(sql) else None)
            return conn
        done = threading.Event()

        def read():
            for project in ('demo', None):
                self.service.state(project)
            done.set()
        with mock.patch.object(usage, 'scan', side_effect=AssertionError('The snapshot must not scan')), \
                mock.patch.object(store, 'connect', side_effect=connect), self.service.write_lock:
            reader = threading.Thread(target=read)
            reader.start()
            reader.join(10)
        self.assertTrue(done.is_set(), 'state() waited for the database writer')
        self.assertEqual(statements, [])
        self.assertIsNone(self.conn.execute("SELECT 1 FROM codex_runs WHERE run_id='late-receipt'").fetchone(),
                          'the receipt waits for the next background step')

    def test_each_step_reads_receipts_records_limits_only_when_they_change_and_matches_the_snapshot(self):
        start = change_feed.latest_seq(self.conn)
        with mock.patch.object(usage, 'scan', wraps=usage.scan) as scan:
            self.assertEqual(self.service.scan_usage(), self.clock)
            [first] = self.limits_changes(start)
            self.assertEqual(first['data'], self.page_fields(self.service.state('demo')))
            # Within the minute only receipts are read, and an unchanged value records nothing.
            self.clock = self.clock.replace(second=30)
            self.write(self.runs, [self.run_record('receipt', cwd=str(self.project))])
            self.service.scan_usage()
            self.assertEqual([call.kwargs['receipts_only'] for call in scan.call_args_list], [False, True])
            self.assertIsNotNone(self.conn.execute("SELECT 1 FROM codex_runs WHERE run_id='receipt'").fetchone())
            self.assertEqual(self.limits_changes(first['seq']), [])
            # A full scan a minute later picks up a new Codex limit and records it once.
            self.rollout('limits', str(self.project), records=[{
                'timestamp': NOW, 'type': 'event_msg', 'payload': {'type': 'token_count', 'info': None, 'rate_limits': {
                    'primary': {'used_percent': 42, 'window_minutes': 10080,
                                'resets_at': self.clock.timestamp() + 7200},
                    'credits': {'balance': '0'}, 'plan_type': 'pro'}}}])
            self.clock = self.clock.replace(minute=self.clock.minute + 1, second=0)
            self.service.scan_usage()
            self.assertFalse(scan.call_args.kwargs['receipts_only'])
        [second] = self.limits_changes(first['seq'])
        self.assertEqual(second['data']['limits']['codex']['used_percent'], 42)
        self.assertEqual(second['data']['usage_scanned_at'], self.clock.isoformat())
        self.assertEqual(second['data'], self.page_fields(self.service.state(None)))

    def test_receipts_refresh_at_least_as_often_as_the_former_poll_and_full_scans_as_before(self):
        self.assertLessEqual(usage_steps.USAGE_TICK_SECONDS, 3)
        self.assertEqual(usage_steps.FULL_SCAN_SECONDS, 60)

    def run_steps(self, steps):
        """Run the background loop for `steps` scan attempts without waiting between them."""
        calls, real = [], usage.scan

        def scan(*args, **kwargs):
            calls.append(kwargs['receipts_only'])
            if len(calls) == steps:
                self.service.stop.set()
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return real(*args, **kwargs)
        with mock.patch.object(usage, 'scan', side_effect=scan), mock.patch.object(usage_steps, 'USAGE_TICK_SECONDS', 0):
            passed = threading.Event()
            passed.set()
            self.service._poll_usage(passed)
        return calls

    def test_a_failed_step_shows_its_real_reason_and_the_next_step_retries_and_clears_it(self):
        start = change_feed.latest_seq(self.conn)
        self.outcomes = [OSError('磁盘暂时不可读'), None]
        with mock.patch('traceback.print_exc') as printed:
            self.assertEqual(self.run_steps(2), [False, False])
        printed.assert_called_once()
        failed, recovered = self.limits_changes(start)
        # The loop ran with no wait between steps, so the retry delay it names is zero here.
        self.assertEqual(failed['data']['usage_error'], '用量扫描失败（0 秒后重试）：OSError: 磁盘暂时不可读')
        self.assertIsNone(failed['data']['usage_scanned_at'])
        self.assertIsNone(recovered['data']['usage_error'])
        self.assertEqual(recovered['data'], self.page_fields(self.service.state('demo')))

    def test_a_page_value_that_cannot_be_built_does_not_hold_back_receipts(self):
        cache = self.claude.parent.parent / '.claude.json'
        self.addCleanup(cache.unlink, missing_ok=True)
        cache.write_text('{"cachedUsageUtilization": "broken"}', encoding='utf-8')
        self.write(self.runs, [self.run_record('receipt', cwd=str(self.project))])
        self.outcomes = [None]
        with mock.patch('traceback.print_exc') as printed:
            self.run_steps(1)
        self.assertEqual(printed.call_count, 2, 'both the step and its error record print their real reason')
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM codex_runs WHERE run_id='receipt'").fetchone())
        self.assertIn('ValueError', self.service.usage_error)
        with self.assertRaisesRegex(ValueError, 'Claude 额度快照必须是对象'):
            self.service.state('demo')
        cache.unlink()
        self.service.stop.clear()
        self.outcomes = [None]
        self.run_steps(1)
        self.assertIsNone(self.service.state('demo')['usage_error'])

    def test_the_first_step_waits_for_the_agents_pass_and_a_stopping_service_ends_the_wait(self):
        passed = threading.Event()
        with mock.patch.object(usage, 'scan', side_effect=AssertionError('No scan before the agents pass')):
            waiting = threading.Thread(target=self.service._poll_usage, args=(passed,))
            waiting.start()
            waiting.join(.8)
            self.assertTrue(waiting.is_alive())
            self.service.stop.set()
            waiting.join(2)
        self.assertFalse(waiting.is_alive())

    def test_the_usage_page_reads_what_the_background_stored(self):
        self.service.scan_usage()
        with mock.patch.object(usage, 'scan', side_effect=AssertionError('The usage page must not scan')):
            report = self.service.usage('week', None, 'demo')
        self.assertIn('summary', report)


class UsageWorkerLifecycleTests(ShellFixture, unittest.TestCase):
    def test_start_primes_the_budget_runs_the_worker_and_close_waits_for_it(self):
        claude_budget.save(self.home, {'amount_usd': 100, 'cycle': 'monthly', 'start': None})
        started = threading.Event()

        def poll(service, after):
            self.assertTrue(after.wait(5), 'the usage scan waits for the first agents pass')
            started.set()
            service.stop.wait()
        with mock.patch.object(web.Service, '_poll_usage', autospec=True, side_effect=poll), \
                mock.patch.object(web.Service, '_poll_agents', autospec=True, side_effect=lambda service, passed: passed.set()), \
                mock.patch.object(web.Service, '_poll_notices'), mock.patch.object(web.Service, '_poll_git_ci'):
            server = web.create_server(self.home, PLUGIN, port=0, cwd=self.project, agents_provider=lambda: [],
                                       claude_root=self.claude, codex_root=self.codex, runs_file=self.runs,
                                       process_factory=self.process_factory)
            try:
                self.assertTrue(started.wait(5))
                service = server.service
                self.assertTrue(service.usage_worker.is_alive())
                # Before any scan finished, the first page already shows the configured budget.
                state = service.state('demo')
                self.assertIsNone(state['usage_scanned_at'])
                self.assertEqual(state['limits']['claude']['budget']['amount_usd'], 100)
            finally:
                server.server_close()
        self.assertFalse(service.usage_worker.is_alive())


if __name__ == '__main__':
    unittest.main()
