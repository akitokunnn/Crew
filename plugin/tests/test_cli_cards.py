"""Session identity and task-card operations on a temporary board."""
import tests  # Install the production-service connection guard.
from test_core import operation_service
import unittest
from crew import board
from crew.ops.errors import OpError
from tests.ops_helpers import ai_call
from test_shell import ShellFixture, NOW


class CliCardsTests(ShellFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.service = operation_service(self, self.project)

    def call(self, name, args=None, sid="s2"):
        return ai_call(self.service, self.conn, sid, name, args)

    def test_board_join_without_task_preserves_current_task(self):
        board.set_task(self.conn, "s2", "当前正在修接口", now=NOW)
        result = board.join(self.conn, "s2", "demo", "two", self.project, self.project, "main", now=NOW)
        self.assertEqual(result["task"], "当前正在修接口")

    def test_card_partial_updates_json_and_does_not_change_current_task(self):
        board.set_task(self.conn, "s2", "现在修聊天", now=NOW)
        first = self.call("session.card.set", {"scope": "接口"})["card"]
        self.assertEqual((first["scope"], first["done_when"]), ("接口", None))
        second = self.call("session.card.set", {"done_when": "测试通过"})["card"]
        self.assertEqual((second["scope"], second["done_when"]), ("接口", "测试通过"))
        third = self.call("session.card.set", {"scope": ""})["card"]
        self.assertEqual((third["scope"], third["done_when"]), ("", "测试通过"))
        self.assertEqual(board.session(self.conn, "s2")["task"], "现在修聊天")

    def test_card_without_fields_reports_error_and_does_not_write(self):
        with self.assertRaises((OpError, board.BoardError, ValueError)):
            self.call('session.card.set', {})
        self.assertIsNone(board.card(self.conn, 's2'))

    def test_card_requires_registered_current_session(self):
        with self.assertRaises(board.BoardError):
            self.call('session.card.set', {'scope': '接口'}, sid='missing')

    def test_whoami_and_who_include_card_in_json_and_text(self):
            board.set_card(self.conn, 's1', '接口', '测试通过', now=NOW)
            own = self.call('session.whoami', sid='s1')
            self.assertEqual(own['leader']['session_id'], 's3')
            for value in (own, self.call('session.who', {'target': 'one'}, sid='s3')):
                self.assertEqual((value['scope'], value['done_when']), ('接口', '测试通过'))

    def test_whoami_and_who_without_card_have_null_fields(self):
            for value in (self.call('session.whoami'), self.call('session.who', {'target': 'two'})):
                self.assertIsNone(value['scope'])
                self.assertIsNone(value['done_when'])
