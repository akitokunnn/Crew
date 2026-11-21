import tests  # Install the production-service connection guard.
import datetime as dt
import unittest

from crew import claude_budget
from test_usage import PRICES, UsageFixture, assistant


MONTHLY = {"amount_usd": 200, "cycle": "monthly", "start": None}


class BudgetSettingsTests(UsageFixture, unittest.TestCase):
    def test_rejects_malformed_settings(self):
        for value in ({"amount_usd": 200, "cycle": "monthly"}, {**MONTHLY, "amount_usd": True}, {**MONTHLY, "amount_usd": 0},
                      {**MONTHLY, "amount_usd": float("inf")}, {**MONTHLY, "cycle": "weekly"},
                      {**MONTHLY, "start": "2026-11-01"}, {**MONTHLY, "cycle": "once"},
                      {**MONTHLY, "cycle": "once", "start": "11/01/2026"}, [200]):
            with self.subTest(value=value), self.assertRaises(claude_budget.BudgetError):
                claude_budget.validate(value)

    def test_save_load_and_clear(self):
        self.assertIsNone(claude_budget.load(self.home))
        once = {"amount_usd": 200, "cycle": "once", "start": "2026-11-04"}
        self.assertEqual(claude_budget.save(self.home, once), {**once, "amount_usd": 200.0})
        self.assertEqual(claude_budget.load(self.home)["start"], "2026-11-04")
        claude_budget.save(self.home, None)
        self.assertIsNone(claude_budget.load(self.home))
        claude_budget.save(self.home, None)

    def test_monthly_period_is_the_utc_calendar_month(self):
        cases = [(dt.datetime(2026, 12, 15, 3, tzinfo=dt.timezone.utc), (2026, 12, 1), (2027, 1, 1)),
                 # 07:00 on 1 Nov in UTC+8 is still October in UTC.
                 (dt.datetime(2026, 11, 1, 7, tzinfo=dt.timezone(dt.timedelta(hours=8))), (2026, 10, 1), (2026, 11, 1))]
        for now, since, reset in cases:
            with self.subTest(now=now):
                self.assertEqual(claude_budget.period(MONTHLY, now),
                                 (dt.datetime(*since, tzinfo=dt.timezone.utc), dt.datetime(*reset, tzinfo=dt.timezone.utc)))

    def test_spending_counts_this_month_and_crew_part(self):
        self.conn.execute("INSERT INTO sessions (session_id, role, project, started_at) VALUES ('leader', 'leader', 'demo', '2026-10-01T00:00:00Z')")
        self.main_file([assistant("leader-oct", 10, tool="a")], "leader")
        # 25 days earlier is September, outside this month's budget.
        self.main_file([assistant("other-sep", 10 - 25 * 86400, tool="b"), assistant("other-oct", 20, tool="c"),
                        assistant("future", 900, tool="d")], "other")
        self.main_file([assistant("unknown", 30, model="claude-unpriced", tool="e")], "third")
        self.scan()
        now = dt.datetime(2026, 10, 21, 0, 1, 40, tzinfo=dt.timezone.utc)
        result = claude_budget.spending(self.conn, claude_budget.validate(MONTHLY), PRICES, now)
        self.assertAlmostEqual(result["spent_usd"], 0.01275 * 2)
        self.assertAlmostEqual(result["crew_usd"], 0.01275)
        self.assertAlmostEqual(result["remaining_usd"], 200 - 0.0255)
        self.assertAlmostEqual(result["used_percent"], 0.0255 / 2)
        self.assertGreater(result["unpriced_tokens"], 0)
        self.assertEqual((result["since"], result["resets_at"]), ("2026-10-01T00:00:00.000Z", "2026-11-01T00:00:00.000Z"))

        once = claude_budget.validate({"amount_usd": 0.02, "cycle": "once", "start": "2026-09-01"})
        result = claude_budget.spending(self.conn, once, PRICES, now)
        self.assertAlmostEqual(result["spent_usd"], 0.01275 * 3)
        self.assertIsNone(result["resets_at"])
        self.assertEqual(result["remaining_usd"], 0.0)
        self.assertGreater(result["used_percent"], 100)


if __name__ == "__main__":
    unittest.main()
