"""Restart recovery with isolated journals and fake Claude processes."""
import json
import unittest
from unittest import mock

from crew import board, chat, models, store
from test_shell import NOW, PLUGIN, ShellFixture


class AutoResumeTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        board.update_cc_name(self.conn, "s3", "cc-lead", now=NOW)

    def interrupted(self, session_id="s1", status="working"):
        self.write(self.manager.path(session_id), [{"seq": 1, "type": "status", "status": status, "at": NOW}])

    def recover(self, agents=(), now=NOW):
        with mock.patch("crew.chat.shutil.which", return_value="claude"):
            self.manager.auto_resume(self.conn, list(agents), now=now)

    def resume_events(self):
        return [event for event in board.events(self.conn, "demo") if event["kind"] == "session:auto-resume"]

    def test_interrupted_member_and_leader_resume_once_with_crew_message_and_current_model(self):
        models.set(self.conn, "s1", "claude-sonnet-5-5", "high", now=NOW)
        self.interrupted("s1")
        self.interrupted("s3")
        self.recover()
        self.assertEqual(len(self.processes), 2)
        for session_id in ("s1", "s3"):
            process = self.manager.processes[session_id].process
            self.assertEqual(process.command[-2:], ["--resume", session_id])
            session = board.session(self.conn, session_id)
            self.assertEqual(process.command[process.command.index("--name") + 1], session["crew_name"])
            self.assertEqual(process.command[process.command.index("--permission-mode") + 1], "bypassPermissions")
            sent = json.loads(process.stdin.getvalue().splitlines()[-1])
            self.assertEqual(sent["message"]["content"], "[Crew] Crew 服务刚重启，你上一轮做到一半被打断了。先看看做到哪了再接着做；如果其实已经做完，就照常收尾、按守则汇报。")
            message = next(event for event in self.manager.journal(session_id).events if event["type"] == "message")
            self.assertEqual(message["source"], "crew")
        self.assertEqual(self.commands[0][self.commands[0].index("--model") + 1], "claude-sonnet-5-5")
        self.assertEqual(self.commands[0][self.commands[0].index("--effort") + 1], "high")
        self.assertNotIn("--model", self.commands[1])
        self.assertNotIn("--effort", self.commands[1])
        self.recover()
        self.assertEqual(len(self.processes), 2)
        self.assertEqual(len(self.resume_events()), 2)
        self.assertEqual({event["detail"]["result"] for event in self.resume_events()}, {"resumed"})

    def test_session_this_service_launched_before_first_poll_is_not_a_restart_leftover(self):
        self.interrupted("s1")
        finished = mock.Mock()
        finished.process.poll.return_value = 0
        self.manager.processes["s1"] = finished
        self.recover()
        self.assertEqual(self.processes, [])
        self.assertEqual(self.resume_events(), [])

    def test_idle_offline_archived_removed_closed_and_disabled_project_do_not_resume(self):
        self.interrupted("s1", "idle")
        self.interrupted("s2", "offline")
        self.interrupted("s3")
        self.conn.execute("INSERT INTO session_controls(session_id,archived_status) VALUES('s3','idle')")
        for session_id, name in (("removed", "removed"), ("closed", "closed"), ("disabled", "disabled")):
            board.join(self.conn, session_id, "demo", name, self.project, self.project, "main", now=NOW)
            self.interrupted(session_id)
        self.conn.execute("INSERT INTO session_controls(session_id,removed) VALUES('removed',1)")
        self.conn.execute("UPDATE sessions SET status='closed' WHERE session_id='closed'")
        self.conn.execute("UPDATE sessions SET project='disabled-project' WHERE session_id='disabled'")
        self.recover()
        self.assertEqual(self.processes, [])
        self.assertEqual(len([event for event in self.conn.execute("SELECT * FROM events WHERE kind='session:auto-resume'")]), 4)
        self.recover()
        self.assertEqual(len([event for event in self.conn.execute("SELECT * FROM events WHERE kind='session:auto-resume'")]), 4)

    def test_live_previous_process_waits_then_resumes_even_if_old_process_writes_offline(self):
        self.interrupted()
        live = {"sessionId": "s1", "name": "old-address"}
        self.recover([live])
        self.assertEqual(self.processes, [])
        self.assertEqual(self.resume_events(), [])
        self.write(self.manager.path("s1"), [{"seq": 2, "type": "status", "status": "offline"}], append=True)
        self.recover(now="2026-10-21T08:05:00.000Z")
        self.assertEqual(len(self.processes), 1)
        self.recover(now="2026-10-21T08:06:00.000Z")
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(len(self.resume_events()), 1)

    def test_live_previous_process_times_out_once_after_thirty_minutes(self):
        self.interrupted()
        live = {"sessionId": "s1", "name": "old-address"}
        self.recover([live])
        self.recover([live], "2026-10-21T08:29:59.000Z")
        self.assertEqual(self.resume_events(), [])
        self.recover([live], "2026-10-21T08:30:00.000Z")
        detail = self.resume_events()[0]["detail"]
        self.assertEqual(detail, {"result": "abandoned", "reason": "活进程在 30 分钟内未退出"})
        self.recover(now="2026-10-21T08:31:00.000Z")
        self.assertEqual(self.processes, [])
        self.assertEqual(len(self.resume_events()), 1)

    def test_fresh_process_check_blocks_race_and_unknown_then_retries(self):
        self.interrupted()
        self.manager.agents_provider = mock.Mock(return_value=[{"sessionId": "s1", "name": "just-opened"}])
        self.recover()
        self.assertEqual(self.processes, [])
        self.manager.agents_provider.side_effect = OSError("agents unavailable")
        self.recover(now="2026-10-21T08:01:00.000Z")
        self.assertEqual(self.processes, [])
        self.manager.agents_provider.side_effect = None
        self.manager.agents_provider.return_value = []
        self.recover(now="2026-10-21T08:02:00.000Z")
        self.assertEqual(len(self.processes), 1)

    def test_manual_wake_while_waiting_cancels_automatic_resume_without_second_launch(self):
        self.interrupted()
        self.recover([{"sessionId": "s1", "name": "old-address"}])
        with mock.patch("crew.chat.shutil.which", return_value="claude"):
            self.manager.wake(self.conn, board.session(self.conn, "s1"), "continue", caller_session_id="s3", now=NOW)
        self.recover()
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.resume_events()[0]["detail"],
                         {"result": "abandoned", "reason": "会话已由网页继续运行"})
        self.recover()
        self.assertEqual(len(self.resume_events()), 1)

    def test_normal_close_with_drained_reader_persists_offline_and_does_not_resume(self):
        with mock.patch("crew.chat.shutil.which", return_value="claude"):
            self.manager.wake(self.conn, board.session(self.conn, "s1"), "continue", caller_session_id="s3", now=NOW)
        self.assertEqual(self.manager.journal("s1").status, "working")
        self.manager.close()
        self.assertTrue(self.manager.processes["s1"].reader_done.wait(2))
        self.assertEqual(chat.Journal(self.manager.path("s1")).status, "offline")
        restarted = chat.Manager(self.home, PLUGIN, lambda: [], self.process_factory)
        self.addCleanup(restarted.close)
        restarted.auto_resume(self.conn, [], now=NOW)
        self.assertEqual(len(self.processes), 1)

    def test_auto_resume_missing_spawn_spec_records_failure_without_launch(self):
        self.interrupted()
        self.conn.execute("UPDATE sessions SET spawn_id='missing' WHERE session_id='s1'")
        self.recover()
        self.assertEqual(self.processes, [])
        detail = self.resume_events()[0]["detail"]
        self.assertEqual(detail["result"], "abandoned")
        self.assertIn("missing.json", detail["reason"])

    def test_close_with_slow_process_keeps_working_then_restart_waits_until_exit(self):
        with mock.patch("crew.chat.shutil.which", return_value="claude"):
            self.manager.wake(self.conn, board.session(self.conn, "s1"), "continue", caller_session_id="s3", now=NOW)
        current = self.manager.processes["s1"]
        process = current.process
        with mock.patch.object(process.stdin, "close"):
            self.manager.close()
        self.assertEqual(chat.Journal(self.manager.path("s1")).status, "working")
        provider = mock.Mock(return_value=[{"sessionId": "s1", "name": "one"}])
        restarted = chat.Manager(self.home, PLUGIN, provider, self.process_factory)
        self.addCleanup(restarted.close)
        restarted.auto_resume(self.conn, provider(), now=NOW)
        self.assertEqual(len(self.processes), 1)
        process.finish()
        self.assertTrue(current.reader_done.wait(2))
        provider.return_value = []
        with mock.patch("crew.chat.shutil.which", return_value="claude"):
            restarted.auto_resume(self.conn, [], now="2026-10-21T08:01:00.000Z")
        self.assertEqual(len(self.processes), 2)

    def test_final_fresh_check_prevents_launch_when_session_reappears_during_preparation(self):
        # First call: wake's online check; second: _launch's fresh check right before starting.
        self.manager.agents_provider = mock.Mock(side_effect=[[], [{"sessionId": "s1", "name": "reopened"}]])
        with self.assertRaisesRegex(board.Conflict, "reopened"):
            self.manager.wake(self.conn, board.session(self.conn, "s1"), "continue", caller_session_id="s3", now=NOW)
        self.assertEqual(self.processes, [])
        self.assertTrue(self.manager.path("s1").exists())
        self.assertEqual(len(list((self.home / "spawn").glob("*.json"))), 1)
        self.assertEqual([event for event in board.events(self.conn, "demo") if event["kind"] == "session:wake"], [])

    def test_wake_with_unknown_leader_address_fails_before_any_spec_log_or_process(self):
        self.conn.execute("UPDATE sessions SET cc_name=NULL WHERE session_id='s3'")
        with self.assertRaisesRegex(board.BoardError, "SendMessage 地址未知"):
            self.manager.wake(self.conn, board.session(self.conn, "s1"), "continue", caller_session_id="s3", now=NOW)
        self.assertEqual(self.processes, [])
        self.assertFalse(self.manager.path("s1").exists())
        self.assertIsNone(board.session(self.conn, "s1")["spawn_id"])
        self.assertFalse((self.home / "spawn").exists())

    def test_online_wake_returns_conflict_even_if_leader_address_is_unknown(self):
        self.conn.execute("UPDATE sessions SET cc_name=NULL WHERE session_id='s3'")
        self.manager.agents_provider = mock.Mock(return_value=[{"sessionId": "s1", "name": "live-address"}])
        with self.assertRaisesRegex(board.Conflict, "VS Code 里在线.*live-address"):
            self.manager.wake(self.conn, board.session(self.conn, "s1"), "continue", caller_session_id="s3", now=NOW)
        self.assertEqual(self.processes, [])
        self.assertIsNone(board.session(self.conn, "s1")["spawn_id"])


if __name__ == "__main__":
    unittest.main()
