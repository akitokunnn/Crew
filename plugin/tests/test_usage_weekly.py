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


class ClaudeWeekHistoryTests(UsageFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.now = dt.datetime(2026, 10, 21, 0, 1, 40, tzinfo=dt.timezone.utc)
        self.conn.execute("INSERT INTO sessions (session_id, role, project, started_at) VALUES ('leader', 'leader', 'demo', ?)", (stamp(),))

    def request(self, session, key, seconds, model="claude-test"):
        path = self.claude / "project" / (session + ".jsonl")
        self.write(path, [assistant(key, seconds, model=model, tool=key + "-tool")], append=path.exists())

    def record(self, reset, percent=25, captured=50):
        usage.record_claude_week(self.conn, {"used_percent": percent, "resets_at": reset, "captured_at": stamp(captured)})

    def weeks(self, now=None, prices=PRICES):
        return usage.claude_weeks(self.conn, prices, now or self.now)

    def test_last_percentage_is_kept_and_crew_is_costed_over_the_whole_window(self):
        week = 7 * 86400
        self.request("leader", "crew", 10)
        self.request("outside", "outside", 20)
        self.scan()
        # The cache reports the same reset as the stream, plus sub-second noise.
        self.record("2026-10-28T00:00:00.034714+00:00")
        self.assertEqual([dict(row) for row in self.conn.execute("SELECT * FROM claude_weeks")],
                         [{"resets_at": stamp(week), "since": stamp(), "used_percent": 25, "captured_at": stamp(50)}])
        [current] = self.weeks()
        self.assertTrue(current["current"])
        self.assertAlmostEqual(current["crew"]["cost_usd"], 0.01275)
        self.assertAlmostEqual(current["crew"]["points"], 0.01275 / 20)
        self.assertAlmostEqual(current["machine_cost_usd"], 0.0255)
        self.assertAlmostEqual(current["crew_share"], 0.5)

        self.request("leader", "late", week - 10)
        self.request("leader", "next-week", week + 10)
        self.scan()
        self.record(stamp(week), percent=40, captured=week - 5)
        self.record(stamp(week), percent=30, captured=100)
        [past] = self.weeks(self.now + dt.timedelta(days=7))
        self.assertEqual((past["used_percent"], past["captured_at"], past["current"]), (40, stamp(week - 5), False))
        self.assertAlmostEqual(past["crew"]["cost_usd"], 0.0255)

    def test_weeks_back_to_crews_first_request_and_between_recorded_windows_have_no_account_percentage(self):
        day = 86400
        for session, key, seconds in (("outside", "before-crew", -20 * day), ("leader", "first", -8 * day),
                                      ("leader", "previous", -day), ("leader", "gap", 9 * day)):
            self.request(session, key, seconds)
        self.scan()
        self.record(stamp(7 * day))
        # Three weeks later the account window has moved by three hours.
        self.record(stamp(28 * day + 3 * 3600), captured=22 * day)
        weeks = self.weeks(self.now + dt.timedelta(days=22))
        self.assertEqual([(week["since"], week["resets_at"]) for week in weeks], [
            (stamp(21 * day + 3 * 3600), stamp(28 * day + 3 * 3600)),
            (stamp(14 * day + 3 * 3600), stamp(21 * day + 3 * 3600)),
            (stamp(7 * day + 3 * 3600), stamp(14 * day + 3 * 3600)),
            (stamp(), stamp(7 * day)), (stamp(-7 * day), stamp()), (stamp(-14 * day), stamp(-7 * day))])
        self.assertEqual([week["used_percent"] for week in weeks], [25, None, None, 25, None, None])
        self.assertEqual([week["current"] for week in weeks], [True] + [False] * 5)
        self.assertEqual([round(week["crew"]["cost_usd"], 5) for week in weeks], [0, 0, 0.01275, 0, 0.01275, 0.01275])

    def test_unpriced_requests_leave_costs_unknown_until_a_price_exists(self):
        self.request("leader", "crew", 10)
        self.request("outside", "new-model", 20, model="claude-new")
        self.scan()
        self.record(stamp(7 * 86400))
        [week] = self.weeks()
        self.assertEqual((week["machine_cost_usd"], week["crew_share"]), (None, None))
        self.assertAlmostEqual(week["crew"]["cost_usd"], 0.01275)
        [week] = self.weeks(prices={**PRICES, "claude-new": PRICES["claude-test"]})
        self.assertAlmostEqual(week["machine_cost_usd"], 0.0255)

    def test_nothing_is_listed_without_a_seen_window(self):
        self.request("leader", "crew", 10)
        self.scan()
        usage.record_claude_week(self.conn, None)
        usage.record_claude_week(self.conn, {"used_percent": 25, "resets_at": None, "captured_at": stamp()})
        self.assertEqual(self.weeks(), [])

if __name__ == "__main__":
    unittest.main()
