"""CLI task cards and join notifications use temporary board data only."""
import json
from unittest import mock

from crew import board
from test_core import CoreCase, NOW


class CliCardsTests(CoreCase):
    def test_board_join_without_task_preserves_current_task(self):
        board.set_task(self.conn, "s2", "当前正在修接口", now=NOW)
        result = board.join(self.conn, "s2", "demo", "two", self.root, self.root, "main", now=NOW)
        self.assertEqual(result["task"], "当前正在修接口")

    def test_cli_join_without_task_preserves_current_task(self):
        board.set_task(self.conn, "s2", "当前正在修接口", now=NOW)
        self.assert_cli(["join", "two"], sid="s2")
        result = json.loads(self.assert_cli(["whoami", "--json"], sid="s2")[1])
        self.assertEqual(result["task"], "当前正在修接口")

    def test_join_explicit_task_updates_task(self):
        board.set_task(self.conn, "s2", "原任务", now=NOW)
        self.assert_cli(["join", "two", "--task", "新任务"], sid="s2")
        self.assertEqual(board.session(self.conn, "s2")["task"], "新任务")
        self.assert_cli(["join", "two", "--task", ""], sid="s2")
        self.assertEqual(board.session(self.conn, "s2")["task"], "")

    def test_join_writes_provided_card_and_queues_notice_to_leader(self):
        self.assert_cli(["join", "lead", "--role", "leader"])
        self.assert_cli(["join", "two", "--group", "lead", "--scope", "接口", "--done-when", "测试通过"], sid="s2")
        card = board.card(self.conn, "s2")
        self.assertEqual((card["scope"], card["done_when"]), ("接口", "测试通过"))
        notices = board.pending_notices(self.conn, "s1")
        self.assertEqual(len(notices), 1)
        self.assertEqual((notices[0]["source"], notices[0]["text"]),
                         ("crew", "two 用 crew join 加入你组，负责：接口。请用 SendMessage 欢迎它并确认分工"))

    def test_join_without_card_does_not_write_card_and_notice_says_unwritten(self):
        self.assert_cli(["join", "lead", "--role", "leader"])
        with mock.patch("crew.cli.board.set_card", wraps=board.set_card) as write_card:
            self.assert_cli(["join", "two", "--group", "lead"], sid="s2")
        write_card.assert_not_called()
        self.assertIsNone(board.card(self.conn, "s2"))
        self.assertEqual(board.pending_notices(self.conn, "s1")[0]["text"],
                         "two 用 crew join 加入你组，负责：未写。请用 SendMessage 欢迎它并确认分工")

    def test_join_card_only_updates_provided_fields(self):
        board.set_card(self.conn, "s2", "接口", "测试通过", now=NOW)
        self.assert_cli(["join", "two", "--scope", "聊天接口"], sid="s2")
        card = board.card(self.conn, "s2")
        self.assertEqual((card["scope"], card["done_when"]), ("聊天接口", "测试通过"))
        self.assert_cli(["join", "two", "--done-when", "全部验收"], sid="s2")
        card = board.card(self.conn, "s2")
        self.assertEqual((card["scope"], card["done_when"]), ("聊天接口", "全部验收"))

    def test_join_ungrouped_or_as_leader_does_not_queue_notice(self):
        self.assert_cli(["join", "lead", "--role", "leader"])
        self.assert_cli(["join", "two", "--scope", "接口"], sid="s2")
        self.assertEqual(board.pending_notices(self.conn, "s1"), [])

    def test_card_partial_updates_json_and_does_not_change_current_task(self):
        board.set_task(self.conn, "s2", "现在修聊天", now=NOW)
        first = json.loads(self.assert_cli(["card", "--scope", "接口", "--json"], sid="s2")[1])
        self.assertEqual((first["scope"], first["done_when"]), ("接口", None))
        second = json.loads(self.assert_cli(["card", "--done-when", "测试通过", "--json"], sid="s2")[1])
        self.assertEqual((second["scope"], second["done_when"]), ("接口", "测试通过"))
        third = json.loads(self.assert_cli(["card", "--scope", "", "--json"], sid="s2")[1])
        self.assertEqual((third["scope"], third["done_when"]), ("", "测试通过"))
        self.assertEqual(board.session(self.conn, "s2")["task"], "现在修聊天")

    def test_card_without_fields_reports_error_and_does_not_write(self):
        for arguments in (["card"], ["card", "--json"]):
            with self.subTest(arguments=arguments):
                error = self.assert_cli(arguments, sid="s2", code=1)[2]
                self.assertIn("请至少提供 --scope 或 --done-when", error)
        self.assertIsNone(board.card(self.conn, "s2"))

    def test_card_requires_registered_current_session(self):
        self.assert_cli(["card", "--scope", "接口"], sid="missing", code=1)
        self.assert_cli(["card", "--scope", "接口"], sid=None, code=1)

    def test_whoami_and_who_include_card_in_json_and_text(self):
        self.assert_cli(["join", "lead", "--role", "leader"])
        self.assert_cli(["roster", "add", "lead", "two"])
        board.set_card(self.conn, "s2", "接口", "测试通过", now=NOW)
        for arguments, sid in ((["whoami"], "s2"), (["who", "two"], "s1")):
            with self.subTest(arguments=arguments):
                value = json.loads(self.assert_cli([*arguments, "--json"], sid=sid)[1])
                self.assertEqual((value["scope"], value["done_when"]), ("接口", "测试通过"))
                if arguments == ["whoami"]:
                    self.assertEqual(value["leader"]["session_id"], "s1")
                text = self.assert_cli(arguments, sid=sid)[1]
                self.assertIn("负责范围：接口", text)
                self.assertIn("完成标准：测试通过", text)

    def test_whoami_and_who_without_card_have_null_fields(self):
        for arguments in (["whoami"], ["who", "two"]):
            with self.subTest(arguments=arguments):
                value = json.loads(self.assert_cli([*arguments, "--json"])[1])
                self.assertIsNone(value["scope"])
                self.assertIsNone(value["done_when"])
                self.assertIn("负责范围：-  完成标准：-", self.assert_cli(arguments)[1])
