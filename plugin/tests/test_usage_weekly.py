import tests  # Install the production-service connection guard.
import datetime as dt
import unittest

from crew import board, goals, runtimes, usage
from test_usage import PRICES, UsageFixture, assistant, stamp, token_count


class CrewWeeklyTests(UsageFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.now = dt.datetime(2026, 10, 21, 0, 1, 40, tzinfo=dt.timezone.utc)
        self.seven_day = {"used_percent": 25, "resets_at": stamp(7 * 86400)}

    def member(self, session, role="member", group=None, project="demo"):
        self.conn.execute(
            "INSERT INTO sessions (session_id, role, group_name, project, started_at) VALUES (?, ?, ?, ?, ?)",
            (session, role, group, project, stamp()))

    def request(self, session, key, seconds=10, model="claude-test"):
        return self.main_file([assistant(key, seconds, model=model, tool=key + "-tool")], session)

    def weekly(self, window=None):
        return usage.crew_weekly(self.conn, self.seven_day if window is None else window, PRICES, self.now)

    def test_window_includes_start_and_now_but_excludes_earlier_and_future_requests(self):
        self.member("leader", role="leader")
        self.main_file([assistant(str(second), second, tool=str(second)) for second in (-1, 0, 100, 101)], "leader")
        self.scan()
        result = self.weekly()
        self.assertAlmostEqual(result["cost_usd"], 0.0255)
        self.assertEqual(result["since"], stamp())

    def test_current_roster_across_projects_includes_subagents_and_excludes_codex(self):
        self.member("leader", role="leader")
        self.member("member", group="one")
        self.member("other-project", group="two", project="other")
        self.member("independent")
        self.member("empty-group", group="")
        for session in ("leader", "member", "other-project", "independent", "empty-group", "unregistered"):
            self.request(session, session)
        child = self.claude / "project" / "member" / "subagents" / "agent-child.jsonl"
        self.write(child, [assistant("child", tool="child-tool")])
        child.with_suffix(".meta.json").write_text('{"agentType": "Explore"}', encoding="utf-8")
        self.rollout(records=[token_count(total=9_000_000)])
        self.scan()
        self.assertAlmostEqual(self.weekly()["cost_usd"], 0.01275 * 4)
        self.conn.execute("UPDATE sessions SET group_name = NULL WHERE session_id = 'member'")
        self.assertAlmostEqual(self.weekly()["cost_usd"], 0.01275 * 2)

    def test_native_general_and_finished_task_bindings_count_for_leader_and_member(self):
        board.init_project(self.conn, 'demo', 'C:\\project', now=stamp())
        for sid, role in [('leader', 'leader'), ('worker', 'member')]:
            board.join(self.conn, sid, 'demo', sid, 'C:\\project', 'C:\\project', 'main',
                       role=role, group_name=None if role == 'leader' else 'leader', now=stamp())
            runtimes.set(self.conn, sid, 'claude', sid + '-native', now=stamp())
            self.request(sid + '-native', sid)
        goal = goals.create(self.conn, 'demo', 'leader', 'Check', 'Complete the check', ['Passed'], now=stamp())
        goals.assign(self.conn, goal['id'], 'worker', 'Check', now=stamp())
        goals.transition(self.conn, goal['id'], 'running', now=stamp())
        execution = goals.create_execution(self.conn, goal['id'], 'worker', runtime='claude', now=stamp())
        goals.bind_native(self.conn, execution['id'], 'claude', 'worker-task', now=stamp())
        goals.set_execution_status(self.conn, execution['id'], 'completed', now=stamp(20))
        self.request('worker-task', 'task')
        self.request('unregistered', 'outside')
        self.scan()
        self.assertAlmostEqual(self.weekly()['cost_usd'], 0.01275 * 3)

    def test_coefficients_and_result_shape(self):
        self.member("leader", role="leader")
        self.request("leader", "priced")
        self.scan()
        result = self.weekly()
        self.assertEqual(set(result), {"cost_usd", "points", "low", "high", "usd_per_point", "usd_per_point_range", "since"})
        self.assertEqual((result["usd_per_point"], result["usd_per_point_range"]), (20, [16, 27]))
        for field, divisor in (("points", 20), ("low", 27), ("high", 16)):
            self.assertAlmostEqual(result[field], 0.01275 / divisor)

    def test_unknown_model_leaves_all_cost_estimates_null(self):
        self.member("leader", role="leader")
        self.request("leader", "unknown", model="unknown")
        self.scan()
        self.assertEqual([self.weekly()[field] for field in ("cost_usd", "points", "low", "high")], [None] * 4)

    def test_unsplit_cache_leaves_cost_estimates_null_as_in_other_usage_reports(self):
        self.member("leader", role="leader")
        record = assistant("legacy", tool="legacy-tool")
        del record["message"]["usage"]["cache_creation"]
        self.main_file([record], "leader")
        self.scan()
        self.assertEqual([self.weekly()[field] for field in ("cost_usd", "points", "low", "high")], [None] * 4)

    def test_missing_expired_and_resetless_windows_have_no_estimate(self):
        for window in (None, {"used_percent": None, "resets_at": stamp(7 * 86400)},
                       {"used_percent": 25, "resets_at": None}, {"used_percent": 25},
                       {"used_percent": 25, "resets_at": stamp(100)},
                       {"used_percent": 25, "resets_at": stamp(99)}):
            with self.subTest(window=window):
                self.assertIsNone(usage.crew_weekly(self.conn, window, PRICES, self.now))

    def test_empty_crew_has_zero_cost(self):
        result = self.weekly()
        self.assertEqual([result[field] for field in ("cost_usd", "points", "low", "high")], [0] * 4)


if __name__ == "__main__":
    unittest.main()
