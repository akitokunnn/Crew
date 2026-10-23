import tests  # Install the production-service connection guard.
import unittest

from crew import session_ops, board, usage
from test_usage import PRICES, UsageFixture, assistant, stamp, token_count


class UsageTreeTests(UsageFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        board.init_project(self.conn, "demo", "C:\\project", now=stamp())

    def join(self, session_id, name, group=None, role="member"):
        return board.join(self.conn, session_id, "demo", name, "C:\\project", "C:\\project", "main",
                          role=role, group_name=group, now=stamp())

    def requests_for(self, session_id, count=1, model="claude-test", seconds=10, cwd="C:\\project"):
        return self.main_file([
            dict(assistant(session_id + str(i), seconds + i, model=model,
                           tool=session_id + "-tool-" + str(i)), cwd=cwd)
            for i in range(count)
        ], session=session_id)

    def tree(self, start=None, end=None, project="demo"):
        return usage.tree(self.conn, start, end, PRICES, project)

    def test_totals_self_members_codex_and_unadopted_bucket(self):
        self.join("leader", "协调", role="leader")
        self.join("session-one", "接口", group="协调")
        self.join("independent", "独立")
        for session_id in ("leader", "session-one", "independent", "unregistered"):
            self.requests_for(session_id, count=2 if session_id == "session-one" else 1)
        self.rollout(records=[token_count(total=3500)])
        self.write(self.runs, [self.run_record(),
                               self.run_record("unmatched", cwd="C:\\project\\other", seconds=200)])
        self.scan()

        team, ungrouped = self.tree()
        self.assertEqual((team["leader_session_id"], team["name"]), ("leader", "协调"))
        self.assertEqual(team["self"]["tokens"], 3600)
        self.assertAlmostEqual(team["self"]["cost"], 0.01275)
        self.assertEqual(team["tokens"], 10800)
        self.assertAlmostEqual(team["cost"], 0.03825)
        self.assertEqual(team["share"], 60.0)
        self.assertEqual(len(team["members"]), 1)
        member = team["members"][0]
        self.assertEqual((member["session_id"], member["name"], member["tokens"], member["share"]),
                         ("session-one", "接口", 7200, 40.0))
        self.assertEqual((member["codex_runs"], member["codex_tokens"]), (2, 3500))
        self.assertEqual((ungrouped["leader_session_id"], ungrouped["name"], ungrouped["tokens"]),
                         (None, "未收编的会话", 7200))
        self.assertEqual({row["session_id"] for row in ungrouped["members"]},
                         {"independent", "unregistered"})
        summary = usage.summary(self.conn, None, None, PRICES, "demo")["claude"]
        self.assertEqual(sum(row["tokens"] for row in self.tree()), summary["total_tokens"])
        self.assertAlmostEqual(sum(row["cost"] for row in self.tree()), summary["cost_usd"])
        self.assertEqual(sum(row["share"] for row in self.tree()), 100.0)

    def test_current_leader_relation_reassigns_historical_usage_and_closed_group_is_unadopted(self):
        self.join("old-leader", "旧组", role="leader")
        self.join("new-leader", "新组", role="leader")
        self.join("worker", "成员", group="旧组")
        self.requests_for("worker")
        self.scan()
        self.assertEqual(self.tree()[0]["leader_session_id"], "old-leader")
        self.join("worker", "成员", group="新组")
        team, ungrouped = self.tree()
        self.assertEqual((team["leader_session_id"], team["self"]),
                         ("new-leader", {"tokens": 0, "cost": 0.0}))
        self.assertEqual(team["members"][0]["session_id"], "worker")
        self.assertEqual(ungrouped["tokens"], 0)
        session_ops.close_group(self.conn, "demo", "新组", now=stamp(100))
        self.assertEqual(len(self.tree()), 1)
        self.assertEqual(self.tree()[0]["members"][0]["session_id"], "worker")

    def test_window_and_project_match_existing_usage_scope(self):
        board.init_project(self.conn, "other", "C:\\other", now=stamp())
        self.join("leader", "协调", role="leader")
        self.join("worker", "成员", group="协调")
        self.requests_for("leader", seconds=10)
        self.requests_for("worker", seconds=30)
        self.requests_for("outside", seconds=30, cwd="C:\\other")
        self.scan()
        tree = self.tree(stamp(20), stamp(40))
        self.assertEqual(tree[0]["self"]["tokens"], 0)
        self.assertEqual(tree[0]["tokens"], 3600)
        self.assertEqual(tree[0]["share"], 100.0)
        self.assertEqual(tree[-1]["tokens"], 0)
        machine = self.tree(stamp(20), stamp(40), project=None)
        self.assertEqual(sum(row["tokens"] for row in machine), 7200)
        self.assertEqual(machine[-1]["members"][0]["session_id"], "outside")
        self.assertEqual([row["share"] for row in machine], [50.0, 50.0])

    def test_unknown_price_remains_null_only_for_affected_group(self):
        self.join("leader", "协调", role="leader")
        self.join("worker", "成员", group="协调")
        self.requests_for("leader")
        self.requests_for("worker", model="unknown")
        self.requests_for("independent")
        self.scan()
        team, ungrouped = self.tree()
        self.assertAlmostEqual(team["self"]["cost"], 0.01275)
        self.assertIsNone(team["members"][0]["cost"])
        self.assertIsNone(team["cost"])
        self.assertAlmostEqual(ungrouped["cost"], 0.01275)

    def test_empty_scope_keeps_final_bucket_with_zero_share(self):
        self.assertEqual(self.tree(), [{
            "leader_session_id": None, "name": "未收编的会话", "tokens": 0,
            "cost": 0.0, "share": 0.0, "self": {"tokens": 0, "cost": 0.0}, "members": [],
        }])


if __name__ == "__main__":
    unittest.main()
