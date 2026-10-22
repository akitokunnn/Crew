"""Account quota snapshots from captured protocol/cache data, without credentials or network."""
import concurrent.futures
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from crew import claude_limits


NOW = "2026-10-21T12:00:00.000Z"
LATER = "2026-10-21T12:01:00.000Z"
RESET = "2026-10-21T13:00:00.000Z"
WEEK_RESET = "2026-10-22T02:00:00.000Z"


def epoch(text):
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def event(**windows):
    return {"type": "rate_limit_event", "rate_limit_info": {"unifiedWindows": windows}}


class ClaudeLimitsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "crew"
        self.cache = self.root / ".claude.json"

    def read(self, now=NOW):
        return claude_limits.read(self.home, cache_path=self.cache, now=now)

    def write_cache(self, windows, captured=NOW):
        self.cache.write_text(json.dumps({"cachedUsageUtilization": {
            "fetchedAtMs": epoch(captured) * 1000, "accountUuid": "not-to-be-copied", "utilization": windows},
            "unrelated_secret": "not-to-be-copied"}), encoding="utf-8")

    def test_cache_percentages_and_timestamps_are_read_without_writing(self):
        self.write_cache({"five_hour": {"utilization": 0, "resets_at": RESET},
                          "seven_day": {"utilization": 94, "resets_at": WEEK_RESET}})
        limits = self.read()
        self.assertEqual(limits["five_hour"], {"used_percent": 0, "resets_at": RESET, "captured_at": NOW,
                                                "source": "claude-cache", "expired": False})
        self.assertEqual(limits["seven_day"]["used_percent"], 94)
        self.assertFalse(self.home.exists())
        self.assertNotIn("not-to-be-copied", json.dumps(limits))

    def test_missing_data_and_windows_stay_unknown(self):
        self.assertEqual(self.read(), {"five_hour": None, "seven_day": None})
        self.write_cache({"five_hour": {"utilization": 5, "resets_at": RESET}, "seven_day": None})
        self.assertIsNone(self.read()["seven_day"])
        self.assertFalse(claude_limits.capture(self.home, event(), now=NOW))
        self.assertFalse(claude_limits.capture(self.home, {"type": "result"}, now=NOW))
        self.assertFalse(self.home.exists())

    def test_stream_fraction_is_normalized_and_only_quota_fields_are_saved(self):
        record = event(five_hour={"utilization": 0.08, "resetsAt": epoch(RESET), "unrelated_secret": "discard"},
                       seven_day={"utilization": 0.92, "resetsAt": epoch(WEEK_RESET)})
        record["unrelated_secret"] = "discard"
        self.assertTrue(claude_limits.capture(self.home, record, now=NOW))
        self.assertEqual(self.read()["five_hour"], {"used_percent": 8, "resets_at": RESET, "captured_at": NOW,
                                                    "source": "stream", "expired": False})
        self.assertEqual(self.read()["seven_day"]["used_percent"], 92)
        saved = (self.home / "claude-limits.json").read_text(encoding="utf-8")
        self.assertNotIn("discard", saved)
        self.assertNotIn("expired", saved)

    def test_newest_observation_is_selected_per_window_without_refreshing_missing_window(self):
        claude_limits.capture(self.home, event(five_hour={"utilization": 0.08, "resetsAt": epoch(RESET)},
                                               seven_day={"utilization": 0.92, "resetsAt": epoch(WEEK_RESET)}), now=NOW)
        self.write_cache({"five_hour": {"utilization": 9, "resets_at": RESET}}, captured=LATER)
        self.assertEqual((self.read()["five_hour"]["used_percent"], self.read()["five_hour"]["source"]), (9, "claude-cache"))
        self.assertEqual(self.read()["seven_day"]["captured_at"], NOW)
        claude_limits.capture(self.home, event(five_hour={"utilization": 0.1, "resetsAt": epoch(RESET)}), now=LATER)
        self.assertEqual(self.read()["five_hour"]["used_percent"], 10)
        self.assertEqual(self.read()["seven_day"]["captured_at"], NOW)
        self.assertFalse(claude_limits.capture(self.home, event(five_hour={"utilization": 0.01}), now=NOW))
        self.assertEqual(self.read()["five_hour"]["used_percent"], 10)

    def test_expired_window_is_unknown_without_mutating_the_recorded_snapshot(self):
        claude_limits.capture(self.home, event(five_hour={"utilization": 0.05, "resetsAt": epoch(RESET)},
                                               seven_day={"utilization": 0.95, "resetsAt": epoch(WEEK_RESET)}), now=NOW)
        path = self.home / "claude-limits.json"
        before = path.read_bytes()
        limits = self.read(now=RESET)
        self.assertEqual(limits["five_hour"], {"used_percent": None, "resets_at": RESET, "captured_at": NOW,
                                                "source": "stream", "expired": True})
        self.assertEqual(limits["seven_day"]["used_percent"], 95)
        self.assertEqual(path.read_bytes(), before)

    def test_explicit_null_is_unknown_and_does_not_become_zero_or_reuse_older_cache(self):
        self.write_cache({"five_hour": {"utilization": 9, "resets_at": RESET}})
        claude_limits.capture(self.home, event(five_hour=None), now=LATER)
        self.assertEqual(self.read()["five_hour"], {"used_percent": None, "resets_at": None, "captured_at": LATER,
                                                    "source": "stream", "expired": False})

    def test_concurrent_sessions_preserve_both_account_windows(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(claude_limits.capture, self.home, event(**{name: {
                "utilization": value, "resetsAt": epoch(RESET)}}), now=NOW)
                for name, value in (("five_hour", 0.05), ("seven_day", 0.95))]
            self.assertTrue(all(result.result() for result in results))
        limits = self.read()
        self.assertEqual((limits["five_hour"]["used_percent"], limits["seven_day"]["used_percent"]), (5, 95))
        self.assertEqual(list(self.home.glob("*.tmp")), [])

    def test_bad_protocol_and_failed_save_leave_previous_snapshot_intact(self):
        claude_limits.capture(self.home, event(five_hour={"utilization": 0.05}), now=NOW)
        path = self.home / "claude-limits.json"
        before = path.read_bytes()
        for value in (True, -1, "0.5", float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                claude_limits.capture(self.home, event(five_hour={"utilization": value}), now=LATER)
        with self.assertRaises(ValueError):
            claude_limits.capture(self.home, event(five_hour=[]), now=LATER)
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
