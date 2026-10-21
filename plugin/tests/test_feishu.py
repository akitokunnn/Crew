"""Feishu transport contracts; every process/API runner is fake, no messages."""
import copy
import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from crew import feishu


OWNER = "ou_owner"
WORKSPACE = "Crew 交互验收"
LEADER = "感知组"
QUESTION = {"id": 42, "question": "先完成哪个部分？", "options": ["先上线", "先补测试"],
            "recommendation": "先补测试", "status": "open"}


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def callback(value):
    return {"type": feishu.CARD_EVENT, "event_id": "event-1", "message_id": "om_card", "chat_id": "oc_private",
            "operator_id": OWNER, "host": "im_message", "action_tag": "button", "action_value": json.dumps(value),
            "token": "private-temporary-callback-token", "card_content": "not needed by this transport"}


def completed(data=None, *, code=0, envelope=None):
    value = {"ok": True, "identity": "bot", "data": {"message_id": "om_card", "chat_id": "oc_private"} if data is None else data} if envelope is None else envelope
    return SimpleNamespace(returncode=code, stdout=json.dumps(value) if code == 0 else "", stderr=json.dumps(value) if code else "")


class CardTests(unittest.TestCase):
    def test_question_preserves_scope_choices_recommendation_and_callback_values(self):
        original = copy.deepcopy(QUESTION)
        card = feishu.question_card(QUESTION, workspace=WORKSPACE, leader=LEADER)
        self.assertEqual(QUESTION, original)
        self.assertEqual(card["schema"], "2.0")
        self.assertFalse(card["config"]["enable_forward"])
        self.assertIn(WORKSPACE, card["header"]["subtitle"]["content"])
        self.assertIn(LEADER, card["header"]["subtitle"]["content"])
        self.assertIn("#42", card["header"]["title"]["content"])
        buttons = [v for v in walk(card) if v.get("tag") == "button"]
        self.assertEqual([v["text"]["content"] for v in buttons], ["先补测试", "先上线"])
        self.assertEqual([v["type"] for v in buttons], ["primary_filled", "default"])
        value = buttons[0]["behaviors"][0]["value"]
        self.assertEqual(value, {"crew_action": "answer", "workspace": WORKSPACE, "leader": LEADER,
                                 "decision_id": 42, "answer": "先补测试"})
        self.assertEqual(feishu.parse_card_action(callback(value), owner_id=OWNER), {
            "workspace": WORKSPACE, "leader": LEADER, "decision_id": 42, "answer": "先补测试",
            "event_id": "event-1", "message_id": "om_card", "chat_id": "oc_private"})

    def test_no_choices_invites_an_explicit_reply_and_does_not_invent_an_answer(self):
        card = feishu.question_card({"id": 1, "question": "你希望怎么调整？"}, workspace=WORKSPACE, leader=LEADER)
        self.assertFalse(any(v.get("tag") == "button" for v in walk(card)))
        self.assertIn("直接回复这条消息", json.dumps(card, ensure_ascii=False))

    def test_question_markup_cannot_inject_mentions_and_long_labels_keep_full_answers(self):
        choice = "引号\"、换行\n" + "很长的答案" * 30
        card = feishu.question_card({"id": 1, "question": "<at id=all> *测试*", "options": [choice]}, workspace=WORKSPACE, leader=LEADER)
        content = json.dumps(card, ensure_ascii=False)
        self.assertNotIn("<at id=all>", content)
        button = next(v for v in walk(card) if v.get("tag") == "button")
        self.assertEqual(len(button["text"]["content"]), 100)
        self.assertEqual(button["behaviors"][0]["value"]["answer"], choice)

    def test_answered_card_removes_buttons_and_distinguishes_saved_from_delivered(self):
        answer = {**QUESTION, "status": "answered", "answer": "先补测试", "delivered_at": None}
        card = feishu.answered_card(answer, workspace=WORKSPACE, leader=LEADER)
        self.assertFalse(any(v.get("tag") == "button" or "behaviors" in v for v in walk(card)))
        self.assertIn("等待会话下一轮接收", json.dumps(card, ensure_ascii=False))
        delivered = feishu.answered_card({**answer, "delivered_at": "2026-10-22T00:00:00Z"}, workspace=WORKSPACE, leader=LEADER)
        self.assertIn("已交给会话", json.dumps(delivered, ensure_ascii=False))
        self.assertNotIn("已读", json.dumps(delivered, ensure_ascii=False))

    def test_invalid_or_oversize_cards_fail_before_any_send(self):
        for changes in ({"id": True}, {"id": 0}, {"question": " "}, {"options": "a,b"}, {"options": [""]},
                        {"status": "answered"}, {"question": "长" * 11000}, {"options": ["选"] * 181}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                feishu.question_card({**QUESTION, **changes}, workspace=WORKSPACE, leader=LEADER)
        with self.assertRaises(ValueError):
            feishu.answered_card({**QUESTION, "answer": "尚未保存"}, workspace=WORKSPACE, leader=LEADER)


class EventTests(unittest.TestCase):
    def setUp(self):
        self.value = {"crew_action": "answer", "workspace": WORKSPACE, "leader": LEADER, "decision_id": 42, "answer": "先上线"}

    def test_unrelated_unauthorized_and_malformed_callback_cannot_write_back(self):
        event = callback(self.value)
        for changes in ({"type": feishu.MESSAGE_EVENT}, {"operator_id": "ou_someone_else"}, {"host": "im_top_notice"},
                        {"action_tag": "input"}, {"action_value": "{"}, {"action_value": "[]"},
                        {"action_value": json.dumps({**self.value, "decision_id": True})},
                        {"action_value": json.dumps({**self.value, "crew_action": "other"})}, {"event_id": ""},
                        {"message_id": ""}, {"action_value": None}):
            with self.subTest(changes=changes):
                self.assertIsNone(feishu.parse_card_action({**event, **changes}, owner_id=OWNER))
        self.assertIsNone(feishu.parse_card_action(None, owner_id=OWNER))
        self.assertNotIn("token", feishu.parse_card_action(event, owner_id=OWNER))

    def test_text_reply_keeps_decoded_content_and_requires_private_owner_reply(self):
        event = {"type": feishu.MESSAGE_EVENT, "sender_id": OWNER, "sender_type": "user", "chat_type": "p2p",
                 "message_type": "text", "message_id": "om_reply", "chat_id": "oc_private", "reply_to": "om_card",
                 "content": "{不用再转 JSON} 先补测试\n保留原文"}
        self.assertEqual(feishu.parse_text_reply(event, owner_id=OWNER), {
            "message_id": "om_reply", "chat_id": "oc_private", "reply_to": "om_card", "answer": event["content"]})
        for changes in ({"sender_id": "ou_other"}, {"sender_type": "bot"}, {"chat_type": "group"},
                        {"message_type": "interactive"}, {"reply_to": ""}, {"content": " "}, {"message_id": None}):
            with self.subTest(changes=changes):
                self.assertIsNone(feishu.parse_text_reply({**event, **changes}, owner_id=OWNER))


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.runner = mock.Mock(return_value=completed())
        self.factory = mock.Mock(return_value=object())
        self.client = feishu.Client(executable="C:/fake/lark-cli.exe", runner=self.runner, process_factory=self.factory)

    def test_text_send_pins_bot_profile_and_keeps_content_out_of_shell_arguments(self):
        text = "中文原文\n$(不执行); & `保留`"
        self.assertEqual(self.client.send_text(OWNER, text, idempotency_key="crew-question-42")["message_id"], "om_card")
        args, options = self.runner.call_args
        command = args[0]
        self.assertEqual(command[:3], ["C:/fake/lark-cli.exe", "--profile", feishu.APP_ID])
        self.assertEqual(command[command.index("--as") + 1], "bot")
        self.assertEqual(command[command.index("--user-id") + 1], OWNER)
        self.assertEqual(command[command.index("--text") + 1], "-")
        self.assertEqual(options["input"], text)
        self.assertNotIn(text, command)
        self.assertNotIn("shell", options)
        self.assertEqual(options["encoding"], "utf-8")
        self.assertEqual(options["timeout"], 30)
        self.assertEqual(options["env"]["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"], "1")

    def test_card_send_and_saved_update_use_correct_json_layers_and_message_id(self):
        self.client.send_question(OWNER, QUESTION, workspace=WORKSPACE, leader=LEADER, idempotency_key="crew-42")
        args, options = self.runner.call_args
        self.assertEqual(args[0][args[0].index("--msg-type") + 1], "interactive")
        self.assertEqual(json.loads(options["input"])["schema"], "2.0")
        self.runner.return_value = completed({})
        self.client.update_answer("om_card", {**QUESTION, "status": "answered", "answer": "先补测试"}, workspace=WORKSPACE, leader=LEADER)
        args, options = self.runner.call_args
        self.assertEqual(args[0][3:6], ["im", "messages", "patch"])
        self.assertEqual(args[0][args[0].index("--message-id") + 1], "om_card")
        outer = json.loads(options["input"])
        card = json.loads(outer["content"])
        self.assertIn("答复已保存", card["header"]["title"]["content"])
        self.assertFalse(any(v.get("tag") == "button" for v in walk(card)))
        self.assertNotIn("token", outer)

    def test_cli_failures_keep_machine_codes_without_leaking_cli_output(self):
        envelope = {"ok": False, "error": {"type": "authorization", "code": 99991679,
                    "message": "sensitive-token-must-not-appear", "missing_scopes": ["im:message:send_as_bot"]}}
        self.runner.return_value = completed(code=3, envelope=envelope)
        with self.assertRaises(feishu.FeishuError) as raised:
            self.client.send_text(OWNER, "hello", idempotency_key="crew-42")
        self.assertEqual(raised.exception.kind, "authorization")
        self.assertEqual(raised.exception.code, 99991679)
        self.assertEqual(raised.exception.missing_scopes, ("im:message:send_as_bot",))
        self.assertNotIn("sensitive-token", str(raised.exception))
        self.runner.assert_called_once()

    def test_missing_or_invalid_success_envelope_is_not_delivery_success(self):
        for value in ([1], {"code": 0}, {"ok": True}, {"ok": True, "data": {}}, {"ok": True, "data": {"message_id": None}}):
            with self.subTest(value=value):
                self.runner.return_value = completed(envelope=value)
                with self.assertRaises(feishu.FeishuError):
                    self.client.send_text(OWNER, "hello", idempotency_key="crew-42")
        self.runner.return_value = SimpleNamespace(returncode=3, stdout="", stderr="secret unstructured text")
        with self.assertRaises(feishu.FeishuError) as raised:
            self.client.send_text(OWNER, "hello", idempotency_key="crew-42")
        self.assertNotIn("secret", str(raised.exception))

    def test_timeout_has_unknown_delivery_result_and_no_automatic_retry(self):
        self.runner.side_effect = subprocess.TimeoutExpired(["lark-cli"], 30)
        with self.assertRaises(feishu.FeishuError) as raised:
            self.client.send_text(OWNER, "hello", idempotency_key="crew-42")
        self.assertEqual(raised.exception.kind, "timeout")
        self.assertIn("投递结果暂未确认", str(raised.exception))
        self.runner.assert_called_once()

    def test_validation_prevents_any_process_for_empty_recipient_or_oversize_key(self):
        for recipient, text, key in (("", "hi", "key"), (OWNER, " ", "key"), (OWNER, "hi", ""), (OWNER, "hi", "k" * 51)):
            with self.subTest(recipient=recipient, key=key), self.assertRaises(ValueError):
                self.client.send_text(recipient, text, idempotency_key=key)
        self.runner.assert_not_called()

    def test_consumers_reuse_cli_bus_and_leave_graceful_lifecycle_with_caller(self):
        for event_key in feishu.EVENT_KEYS:
            process = self.client.start_consumer(event_key)
            self.assertIs(process, self.factory.return_value)
            args, options = self.factory.call_args
            self.assertEqual(args[0][3:7], ["event", "consume", event_key, "--as"])
            self.assertEqual(args[0][-2:], ["--jq", "del(.token,.card_content)"])
            for flag in ("--quiet", "--timeout", "--max-events"):
                self.assertNotIn(flag, args[0])
            self.assertEqual([options[p] for p in ("stdin", "stdout", "stderr")], [subprocess.PIPE] * 3)
            self.assertEqual(options["bufsize"], 1)
            self.assertEqual(options["encoding"], "utf-8")
        with self.assertRaises(ValueError):
            self.client.start_consumer("unknown.event")

    def test_native_cli_resolution_uses_existing_npm_binary_without_batch_shell(self):
        with mock.patch("crew.feishu.shutil.which", return_value="C:/npm/lark-cli.CMD"):
            client = feishu.Client(runner=self.runner)
        self.assertEqual(Path(client.executable), Path("C:/npm/node_modules/@larksuite/cli/bin/lark-cli.exe"))
        with mock.patch("crew.feishu.shutil.which", return_value=None), self.assertRaises(feishu.FeishuError):
            feishu.Client()

    def test_process_failure_is_explicit_for_send_and_consumer_start(self):
        self.runner.side_effect = OSError("not found")
        self.factory.side_effect = OSError("not found")
        with self.assertRaises(feishu.FeishuError) as send_error:
            self.client.send_text(OWNER, "hello", idempotency_key="crew-42")
        self.assertEqual(send_error.exception.kind, "process")
        with self.assertRaises(feishu.FeishuError) as start_error:
            self.client.start_consumer(feishu.CARD_EVENT)
        self.assertEqual(start_error.exception.kind, "process")


if __name__ == "__main__":
    unittest.main()
