"""Feishu bot messages and CLI-bus consumers; credentials remain with lark-cli.

Card callbacks are acknowledged by the CLI before they reach Python. The caller
must validate the decision against its board, save the answer, then update the
card. A successful transport acknowledgement is never a saved-answer receipt.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

APP_ID = "cli_aabea57f7538dcef"
MESSAGE_EVENT = "im.message.receive_v1"
CARD_EVENT = "card.action.trigger"
EVENT_KEYS = (MESSAGE_EVENT, CARD_EVENT)


class FeishuError(RuntimeError):
    def __init__(self, message, *, kind="cli", code=None, missing_scopes=()):
        super().__init__(message)
        self.kind, self.code, self.missing_scopes = kind, code, tuple(missing_scopes)


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + "不能为空")
    return value


def _decision_id(value):
    if type(value) is not int or value <= 0:
        raise ValueError("待拍板编号必须是正整数")
    return value


def _markdown(value):
    # Text from the board must not introduce Feishu mentions or card markup.
    return "".join("&#%s;" % ord(c) if c in "&<>*~[]()#:_`\\" else c for c in value)


def _block(elements, background):
    return {"tag": "column_set", "flex_mode": "none", "columns": [{"tag": "column", "width": "weighted",
            "weight": 1, "background_style": background, "padding": "12px", "vertical_spacing": "8px",
            "elements": elements}]}


def _base_card(workspace, leader, decision_id, answered):
    _text(workspace, "工作区")
    _text(leader, "leader")
    if decision_id is not None:
        _decision_id(decision_id)
    return {"schema": "2.0", "config": {"update_multi": True, "width_mode": "default", "enable_forward": False},
            "header": {"title": {"tag": "plain_text", "content": ("答复已保存" if answered else "有件事等你拍板") + (" · #%s" % decision_id if decision_id is not None else "")},
                       "subtitle": {"tag": "plain_text", "content": "工作区：%s · leader：%s" % (workspace, leader)},
                       "template": "green" if answered else "yellow",
                       "icon": {"tag": "standard_icon", "token": "approval_colorful"}},
            "body": {"direction": "vertical", "padding": "12px 12px 20px 12px", "vertical_spacing": "12px", "elements": []}}


def _card_json(card):
    content = json.dumps(card, ensure_ascii=False, separators=(",", ":"))
    if len(content.encode("utf-8")) > 30 * 1024:
        raise ValueError("问题或答复太长，飞书卡片不能超过 30 KB")
    return content


def question_card(decision, *, workspace, leader):
    """Build one pending decision card, without any I/O or hidden state."""
    identity = _decision_id(decision["id"])
    question = _text(decision["question"], "问题")
    if decision.get("status", "open") != "open":
        raise ValueError("只能发送尚未答复的待拍板")
    options = decision.get("options", [])
    if not isinstance(options, (list, tuple)):
        raise ValueError("选项必须是文字数组")
    if len(options) > 180:
        raise ValueError("选项过多，无法放进一张飞书卡片")
    for option in options:
        _text(option, "选项")
    recommendation = decision.get("recommendation")
    if recommendation is not None:
        _text(recommendation, "建议")
    card = _base_card(workspace, leader, identity, False)
    details = [{"tag": "markdown", "content": "**" + _markdown(question) + "**", "text_size": "heading-3"}]
    if recommendation:
        details.append({"tag": "markdown", "content": "会话建议：" + _markdown(recommendation)})
    elements = [_block(details, "yellow-50")]
    if options:
        ordered = list(options)
        if recommendation in ordered:
            ordered.remove(recommendation)
            ordered.insert(0, recommendation)
        buttons = []
        for index, option in enumerate(ordered):
            buttons.append({"tag": "button", "text": {"tag": "plain_text", "content": option if len(option) <= 100 else option[:99] + "…"},
                            "type": "primary_filled" if index == 0 else "default", "width": "fill",
                            "behaviors": [{"type": "callback", "value": {"crew_action": "answer", "workspace": workspace,
                                           "leader": leader, "decision_id": identity, "answer": option}}]})
        elements.append(_block(buttons, "default"))
    elements.append({"tag": "markdown", "text_size": "notation", "content": "请发送「#%s 答复」即可拍板，无需引用消息。普通文字会发给当前 leader。答复保存后，会话会在下一轮接收。" % identity})
    card["body"]["elements"] = elements
    _card_json(card)
    return card


def answered_card(decision, *, workspace, leader):
    """Show saved/handed-over states accurately and remove all answer buttons."""
    if decision.get("status", "answered") != "answered":
        raise ValueError("只能更新已保存答复的卡片")
    question = _text(decision["question"], "问题")
    answer = _text(decision["answer"], "答复")
    card = _base_card(workspace, leader, _decision_id(decision["id"]), True)
    state = "已交给会话，实际进展以会话回复为准。" if decision.get("delivered_at") else "已保存，等待会话下一轮接收。"
    card["body"]["elements"] = [
        _block([{"tag": "markdown", "content": _markdown(question)}], "grey-50"),
        _block([{"tag": "markdown", "content": "**你的答复**\n" + _markdown(answer)}], "green-50"),
        {"tag": "markdown", "text_size": "notation", "content": state}]
    _card_json(card)
    return card


def questions_card(entries):
    """Scoped questions and plain progress notes share one message and saved-state update."""
    if not isinstance(entries, (list, tuple)) or not entries:
        raise ValueError("合并卡片至少需要一条事项")
    cards, seen, pending, answered = [], set(), 0, 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("合并卡片的事项记录无效")
        workspace, leader = _text(entry.get("workspace"), "工作区"), _text(entry.get("leader"), "leader")
        decision = entry.get("decision")
        if decision is None:
            text = _text(entry.get("text"), "进展内容")
            card = _base_card(workspace, leader, None, False)
            label = "工作区：%s · leader：%s" % (workspace, leader)
            cards.append((card, [{"tag": "markdown", "content": "**" + _markdown(label) + "**"},
                                 _block([{"tag": "markdown", "content": _markdown(text)}], "grey-50")]))
            continue
        if not isinstance(decision, dict):
            raise ValueError("合并卡片的待拍板记录无效")
        identity = _decision_id(decision.get("id"))
        key = (workspace, identity)
        if key in seen:
            raise ValueError("合并卡片不能重复包含同一条待拍板")
        seen.add(key)
        status = decision.get("status", "open")
        if status == "open":
            card = question_card(decision, workspace=workspace, leader=leader)
            pending += 1
            # A shared footer explains how to identify a question in a text reply.
            elements = card["body"]["elements"][:-1]
            if entry.get("buttons_enabled", True) is False:
                elements = elements[:1]
                if decision.get("options"):
                    choices = "\n".join("• " + _markdown(option) for option in decision["options"])
                    elements.append(_block([{"tag": "markdown", "content": "可选答复：\n" + choices}], "default"))
                elements.append({"tag": "markdown", "text_size": "notation",
                    "content": "当前未启用卡片按钮，请发送「#%s 答复」，无需引用消息。" % identity})
        elif status == "answered":
            card = answered_card(decision, workspace=workspace, leader=leader)
            elements = card["body"]["elements"]
            answered += 1
        else:
            raise ValueError("合并卡片只能包含未答复或已保存答复的待拍板")
        label = "#%s · 工作区：%s · leader：%s" % (identity, workspace, leader)
        cards.append((card, [{"tag": "markdown", "content": "**" + _markdown(label) + "**"}, *elements]))
    result = cards[0][0]
    result["header"]["title"]["content"] = "有 %s 件事等你拍板" % pending if pending else ("本次答复已保存" if answered else "会话有新进展")
    result["header"]["subtitle"]["content"] = "共 %s 条事项" % len(entries) + ("，每题分别答复" if pending else "")
    result["header"]["template"] = "yellow" if pending else ("green" if answered else "blue")
    result["body"]["elements"] = [element for _, elements in cards for element in elements]
    if pending:
        result["body"]["elements"].append({"tag": "markdown", "text_size": "notation",
            "content": "请发送「#题号 答复」，例如「#42 继续」，无需引用消息。普通文字会发给当前 leader。答复保存后，会话会在下一轮接收。"})
    _card_json(result)
    return result


def parse_card_action(event, *, owner_id):
    """Recognize an owner's Crew button; board authorization stays with caller."""
    _text(owner_id, "收件人")
    if not isinstance(event, dict) or event.get("type") != CARD_EVENT or event.get("operator_id") != owner_id:
        return None
    if event.get("action_tag") != "button" or event.get("host") != "im_message":
        return None
    try:
        value = json.loads(event["action_value"])
        if not isinstance(value, dict) or value.get("crew_action") != "answer":
            return None
        answer = {"workspace": _text(value["workspace"], "工作区"), "leader": _text(value["leader"], "leader"),
                  "decision_id": _decision_id(value["decision_id"]), "answer": _text(value["answer"], "答复")}
        for key in ("event_id", "message_id", "chat_id"):
            answer[key] = _text(event[key], key)
        return answer
    except (ValueError, TypeError, KeyError):
        return None


def parse_private_text(event, *, owner_id):
    """Validate owner private text; preserve decoded content and an optional quote."""
    _text(owner_id, "收件人")
    if not isinstance(event, dict) or event.get("type") != MESSAGE_EVENT or event.get("sender_id") != owner_id:
        return None
    if event.get("sender_type") != "user" or event.get("chat_type") != "p2p" or event.get("message_type") != "text":
        return None
    try:
        reply_to = event.get("reply_to")
        if reply_to is not None:
            _text(reply_to, "原消息")
        return {"message_id": _text(event["message_id"], "消息"), "chat_id": _text(event["chat_id"], "会话"),
                "reply_to": reply_to, "answer": _text(event["content"], "正文")}
    except (ValueError, KeyError):
        return None


def _executable():
    path = shutil.which("lark-cli")
    if path is None:
        raise FeishuError("找不到 lark-cli，请先检查本机飞书 CLI 安装")
    if Path(path).suffix.lower() == ".cmd":
        # Invoke the existing npm package's binary; no batch shell or second SDK.
        path = str(Path(path).parent / "node_modules" / "@larksuite" / "cli" / "bin" / "lark-cli.exe")
    return path


class Client:
    def __init__(self, profile=APP_ID, *, executable=None, runner=subprocess.run, process_factory=subprocess.Popen):
        self.profile, self.executable = _text(profile, "飞书配置"), _executable() if executable is None else str(executable)
        self.runner, self.process_factory = runner, process_factory

    def _command(self, *args):
        return [self.executable, "--profile", self.profile, *args]

    def _options(self):
        env = dict(os.environ, LARKSUITE_CLI_NO_UPDATE_NOTIFIER="1", LARKSUITE_CLI_NO_SKILLS_NOTIFIER="1")
        return {"text": True, "encoding": "utf-8", "env": env,
                "creationflags": subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0}

    def _invoke(self, command, content):
        try:
            result = self.runner(command, input=content, capture_output=True, timeout=30, **self._options())
        except subprocess.TimeoutExpired as exc:
            raise FeishuError("飞书请求超时，投递结果暂未确认，请检查投递记录", kind="timeout") from exc
        except OSError as exc:
            raise FeishuError("无法运行本机飞书 CLI，请检查安装", kind="process") from exc
        try:
            envelope = json.loads(result.stdout if result.returncode == 0 else result.stderr)
        except (ValueError, TypeError):
            raise FeishuError("飞书 CLI 返回了无法识别的结果", kind="protocol") from None
        if not isinstance(envelope, dict):
            raise FeishuError("飞书 CLI 返回了无法识别的结果", kind="protocol")
        if result.returncode != 0 or envelope.get("ok") is not True:
            error = envelope.get("error", {})
            error = error if isinstance(error, dict) else {}
            kind = error.get("type")
            kind = kind if kind in ("authorization", "api", "network", "validation", "confirmation") else "cli"
            code = error.get("code") if type(error.get("code")) is int else None
            raw_scopes = error.get("missing_scopes", [])
            scopes = [s for s in raw_scopes if isinstance(s, str) and ":" in s] if isinstance(raw_scopes, list) else []
            message = "飞书授权不可用，请检查本机 CLI 与应用权限" if kind == "authorization" else "飞书操作失败，请检查本机 CLI 与投递记录"
            raise FeishuError(message + ("（%s）" % code if code is not None else ""), kind=kind, code=code, missing_scopes=scopes)
        data = envelope.get("data")
        if not isinstance(data, dict):
            raise FeishuError("飞书 CLI 返回结果缺少 data", kind="protocol")
        return data

    def _send(self, owner_id, flag, content, idempotency_key, msg_type=None):
        _text(owner_id, "收件人")
        _text(idempotency_key, "幂等编号")
        if len(idempotency_key) > 50:
            raise ValueError("飞书幂等编号不能超过 50 个字符")
        args = ["im", "+messages-send", "--as", "bot", "--user-id", owner_id,
                "--idempotency-key", idempotency_key, flag, "-"]
        if msg_type is not None:
            args += ["--msg-type", msg_type]
        data = self._invoke(self._command(*args), content)
        if not isinstance(data.get("message_id"), str) or not data["message_id"]:
            raise FeishuError("飞书发送结果缺少消息编号，尚不能确认投递", kind="protocol")
        return data

    def send_text(self, owner_id, text, *, idempotency_key):
        return self._send(owner_id, "--text", _text(text, "消息"), idempotency_key)

    def send_question(self, owner_id, decision, *, workspace, leader, idempotency_key):
        card = question_card(decision, workspace=workspace, leader=leader)
        return self._send(owner_id, "--content", _card_json(card), idempotency_key, "interactive")

    def send_questions(self, owner_id, entries, *, idempotency_key):
        return self._send(owner_id, "--content", _card_json(questions_card(entries)), idempotency_key, "interactive")

    def _update_card(self, message_id, card):
        _text(message_id, "消息编号")
        return self._invoke(self._command("im", "messages", "patch", "--as", "bot", "--message-id", message_id,
                                          "--data", "-"), json.dumps({"content": _card_json(card)}, ensure_ascii=False))

    def update_answer(self, message_id, decision, *, workspace, leader):
        return self._update_card(message_id, answered_card(decision, workspace=workspace, leader=leader))

    def update_questions(self, message_id, entries):
        return self._update_card(message_id, questions_card(entries))

    def consumer_command(self, event_key):
        if event_key not in EVENT_KEYS:
            raise ValueError("不支持的飞书事件")
        return self._command("event", "consume", event_key, "--as", "bot", "--jq", "del(.token,.card_content)")

    def start_consumer(self, event_key):
        """Caller drains both pipes, waits on ready marker and closes stdin on exit.

        Do not set --timeout/--max-events: bounded consumers ignore stdin EOF.
        Do not stop the shared CLI bus, which may serve other local consumers.
        """
        command = self.consumer_command(event_key)
        try:
            return self.process_factory(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, bufsize=1, **self._options())
        except OSError as exc:
            raise FeishuError("无法启动飞书事件接收，请检查本机 CLI", kind="process") from exc
