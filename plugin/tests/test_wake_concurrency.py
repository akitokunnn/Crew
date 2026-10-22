"""Concurrent wake paths must serialize before launching a session."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import threading
import unittest
from unittest import mock

from crew import board, store
from test_shell import NOW, ShellFixture


class WakeConcurrencyTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        board.update_cc_name(self.conn, "s3", "cc-lead", now=NOW)

    def test_two_wake_requests_launch_only_one_process(self):
        ready = threading.Barrier(2)

        def wake():
            with closing(store.connect(self.home)) as conn:
                target = board.session(conn, "s1")
                ready.wait(timeout=5)
                try:
                    self.manager.wake(conn, target, "继续", caller_session_id="s3", now=NOW)
                except board.Conflict:
                    return False
                return True

        with mock.patch("crew.chat.shutil.which", return_value="claude.exe"), ThreadPoolExecutor(2) as workers:
            results = list(workers.map(lambda _: wake(), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(len(list((self.home / "spawn").glob("*.json"))), 1)
        self.assertEqual(sum(event["kind"] == "session:wake" for event in board.events(self.conn, "demo")), 1)
        message = next(event for event in self.manager.journal("s1").events if event["type"] == "message")
        self.assertEqual((message["source"], message["sender"]), ("crew", "lead"))

    def test_auto_resume_and_wake_launch_only_one_process(self):
        self.manager.journal("s1").set_status("working")
        ready = threading.Barrier(2)

        def wake():
            with closing(store.connect(self.home)) as conn:
                target = board.session(conn, "s1")
                ready.wait(timeout=5)
                try:
                    self.manager.wake(conn, target, "继续", now=NOW)
                except board.Conflict:
                    pass

        def resume():
            with closing(store.connect(self.home)) as conn:
                ready.wait(timeout=5)
                self.manager.auto_resume(conn, [], now=NOW)

        with mock.patch("crew.chat.shutil.which", return_value="claude.exe"), ThreadPoolExecutor(2) as workers:
            futures = [workers.submit(wake), workers.submit(resume)]
            for future in futures:
                future.result(timeout=10)
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(len(list((self.home / "spawn").glob("*.json"))), 1)
        messages = [event for event in self.manager.journal("s1").events if event["type"] == "message"]
        self.assertEqual(len(messages), 1)
        self.manager.auto_resume(self.conn, [], now=NOW)
        self.assertEqual(len(self.processes), 1)
        events = [event for event in board.events(self.conn, "demo") if event["kind"] == "session:auto-resume"]
        self.assertEqual(len(events), 1)
