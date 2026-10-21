import contextlib
import csv
import io
import json
import unittest
from pathlib import Path

from crew import usage_cli
from test_usage import PRICES, UsageFixture, assistant, prompt


class TestUsageCLI(UsageFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.main_file([prompt("p"), assistant("one")])
        self.rollout()
        self.write(self.runs, [self.run_record()])
        self.board_session()
        (self.home / "prices.json").write_text(json.dumps(PRICES), encoding="utf-8")

    def invoke(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = usage_cli.main([*args, "--claude-root", str(self.claude), "--codex-root", str(self.codex),
                                   "--runs-file", str(self.runs), "--period", "all"], self.home)
        self.assertEqual(code, 0)
        return output.getvalue()

    def test_every_command_json_and_text(self):
        for command in ("scan", "summary", "sessions", "daily", "codex", "agents", "models"):
            with self.subTest(command=command):
                value = json.loads(self.invoke(command, "--json"))
                self.assertIsInstance(value, dict if command in ("scan", "summary") else list)
                self.assertTrue(self.invoke(command).strip())
        self.assertEqual(json.loads(self.invoke("summary", "--json"))["claude"]["requests"], 1)
        self.assertIn("\u89c6\u89c9", self.invoke("sessions", "--json"))

    def test_automatic_scan_for_every_report(self):
        commands = ("summary", "sessions", "daily", "codex", "agents", "models")
        for index, command in enumerate(commands):
            self.write(self.claude / "project" / "session-one.jsonl",
                       [assistant("added-" + str(index), 20 + index, tool="tool-added-" + str(index))], append=True)
            self.invoke(command, "--json")
            self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM claude_requests").fetchone()[0], index + 2)

    def test_each_export_bom_headers_and_values(self):
        for what in ("sessions", "daily", "codex", "requests"):
            with self.subTest(what=what):
                path = self.root / (what + ".csv")
                result = json.loads(self.invoke("export", "--what", what, "--out", str(path), "--json"))
                self.assertEqual(result["rows"], 1)
                self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
                with path.open(encoding="utf-8-sig", newline="") as stream:
                    reader = csv.DictReader(stream)
                    self.assertEqual(reader.fieldnames, usage_cli.EXPORT_FIELDS[what])
                    rows = list(reader)
                self.assertEqual(rows[0]["display_name"], "\u89c6\u89c9")
                self.assertIn(",", rows[0]["total_tokens"])
                if "cost_usd" in rows[0]:
                    self.assertEqual(rows[0]["cost_usd"], "0.01")
                self.assertIn("\u5df2\u5bfc\u51fa", self.invoke("export", "--what", what, "--out", str(path)))

    def test_empty_exports_still_have_stable_headers(self):
        for what in usage_cli.EXPORT_FIELDS:
            path = self.root / ("empty-" + what + ".csv")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                usage_cli.main(["--claude-root", str(self.claude), "--codex-root", str(self.codex), "--runs-file", str(self.runs),
                                "export", "--what", what, "--out", str(path), "--period", "2000-01-01..2000-01-02", "--json"], self.home)
            self.assertEqual(json.loads(output.getvalue())["rows"], 0)
            self.assertEqual(path.read_text(encoding="utf-8-sig").strip(), ",".join(usage_cli.EXPORT_FIELDS[what]))

    def test_text_chinese_width_numeric_money_format(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            usage_cli._table([{"display_name": "\u89c6\u89c9", "total_tokens": 1234567, "cost_usd": 1234.5},
                              {"display_name": "abc", "total_tokens": 12, "cost_usd": None}],
                             ["display_name", "total_tokens", "cost_usd"])
        lines = output.getvalue().splitlines()
        self.assertIn("1,234,567", lines[2])
        self.assertIn("1,234.50", lines[2])
        self.assertEqual(usage_cli._width(lines[2].split("1,234,567")[0]), usage_cli._width(lines[3].split("12")[0]))
        self.assertEqual(usage_cli._width("\u89c6\u89c9abc"), 7)

    def test_price_file_missing_is_not_silently_defaulted(self):
        (self.home / "prices.json").unlink()
        with self.assertRaises(FileNotFoundError):
            self.invoke("summary", "--json")

    def test_project_option_filters_reports_and_exports_in_either_argument_position(self):
        self.conn.executemany("INSERT INTO projects (name, root, created_at) VALUES (?, ?, ?)",
                              [("demo", "C:\\project", "2026-10-21T00:00:00Z"),
                               ("elsewhere", "C:\\elsewhere", "2026-10-21T00:00:00Z")])
        self.main_file([dict(assistant("outside"), cwd="C:\\elsewhere")], "outside-session")
        self.rollout("outside-roll", cwd="C:\\elsewhere")
        for command in ("summary", "sessions", "daily", "codex", "agents", "models"):
            with self.subTest(command=command):
                before = json.loads(self.invoke("--project", "demo", command, "--json"))
                after = json.loads(self.invoke(command, "--project", "demo", "--json"))
                self.assertEqual(before, after)
                if command == "summary":
                    self.assertEqual((after["claude"]["requests"], after["codex"]["runs"]), (1, 1))
                else:
                    self.assertEqual(len(after), 2 if command == "models" else 1)
        for what in usage_cli.EXPORT_FIELDS:
            path = self.root / ("scoped-" + what + ".csv")
            result = json.loads(self.invoke("export", "--project", "demo", "--what", what, "--out", str(path), "--json"))
            self.assertEqual(result["rows"], 1)
        self.assertEqual(json.loads(self.invoke("summary", "--json"))["claude"]["requests"], 2)


if __name__ == "__main__":
    unittest.main()
