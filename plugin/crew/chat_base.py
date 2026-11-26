import base64
import functools
import os

from . import board


PAUSED_FOR_UPDATE = "Crew 正在准备更新，暂不接受新任务；更新完成后再试"
MAINTENANCE_HELD = "正在压缩该人员的记忆，完成后再发送"
# crew send defaults to information, which is only archived; a reply must say what it is.
REPLY_KIND = "--kind result（阻塞用 blocker，要对方做事用 task）"
# Claude's own answer when --resume names a conversation it does not have.
CLAUDE_NO_CONVERSATION = "No conversation found with session ID: "
NATIVE_MISSING = ("原会话丢失：Claude 报告找不到原生会话 %s。Crew 会新开一个原生会话并交接任务背景；"
                  "新会话不是原会话，原会话里的上下文不会接回。")
NATIVE_REPLACED = "这是新开的原生会话：原会话 %s 已丢失，Crew 已交接任务背景；原会话里的上下文没有接回。"
NATIVE_LOST_INPUT = "native-lost:"
# Journal events only a running native conversation can produce.
NATIVE_OUTPUT = {"assistant", "text", "tool_result", "compact"}
# An owned native idle this long is closed; the next input resumes the same
# native ID. CREW_IDLE_RECLAIM_MINUTES overrides it, 0 keeps every process.
IDLE_RECLAIM_MINUTES = 30


def idle_reclaim_seconds(environ=None):
    value = (os.environ if environ is None else environ).get('CREW_IDLE_RECLAIM_MINUTES')
    try:
        minutes = IDLE_RECLAIM_MINUTES if value is None or not value.strip() else float(value)
    except ValueError:
        minutes = IDLE_RECLAIM_MINUTES
    return max(0.0, minutes) * 60


def track_background(current, record):
    """Background shells and agents outlive the turn's result inside the native process."""
    task = record.get('task_id')
    if record.get('type') != 'system' or not task or record.get('ambient'):
        return
    subtype = record.get('subtype')
    if subtype == 'task_started':
        current.background_tasks.add(task)
    elif subtype == 'task_notification' or (subtype == 'task_updated' and
            (record.get('patch') or {}).get('status') not in (None, 'pending', 'running')):
        current.background_tasks.discard(task)


def native_output(event):
    return event['type'] in NATIVE_OUTPUT or event['type'] == 'turn_result' and event.get('success')


def has_native_output(events):
    return any(native_output(event) for event in events)


def lost_turn(events):
    """Text for the new native: the inputs the lost conversation never answered."""
    pending = []
    for event in reversed(events):
        if native_output(event):
            break
        if event['type'] == 'message' and not event['id'].startswith(NATIVE_LOST_INPUT):
            pending.append(event)
    if not pending:
        return ('[Crew] 原会话丢失后新开的会话。原会话没有留下未处理的输入；'
                '请按交接材料核对当前进展后继续，已完成的部分不要重做。')
    lines = ['[Crew] 原会话丢失后新开的会话。以下是原会话丢失前尚未得到处理的输入（按时间顺序），请在新会话中处理：']
    for event in reversed(pending):
        lines.append('（来源：%s%s，消息 %s）\n%s' % (event.get('source', 'human'),
                     ' · ' + event['sender'] if event.get('sender') else '', event['id'], event['text']))
    return '\n\n'.join(lines)


class UpdatePaused(board.Conflict):
    """New model work is refused while a desktop update holds the gate."""


def _admitted(method):
    # Every entry that can start or feed a model turn passes the update gate.
    @functools.wraps(method)
    def admitted(self, *args, **kwargs):
        with self.work():
            return method(self, *args, **kwargs)
    return admitted


IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def compact_command(text):
    return isinstance(text, str) and (text.strip() == "/compact" or
        text.lstrip().startswith("<command-name>/compact</command-name>"))


def tool_label(name, arguments):
    if name == "Bash":
        label = arguments.get("description") or arguments.get("command") or name
    elif name in ("Read", "Edit", "Write"):
        path = arguments.get("file_path") or arguments.get("path")
        label = path.replace("\\", "/").rsplit("/", 1)[-1] if path else name
    elif name in ("Grep", "Glob"):
        label = arguments.get("pattern") or name
    elif name in ("Agent", "Task"):
        label = arguments.get("description") or name
    elif name == "SendMessage":
        label = "发消息给 " + arguments["to"]
    elif name == "Skill":
        label = arguments.get("skill") or name
    else:
        label = name
    return label[:60]


def validate_images(images):
    if images is None:
        return []
    if not isinstance(images, list):
        raise board.BoardError("images 必须是图片列表")
    if len(images) > 5:
        raise board.BoardError("一次最多发送 5 张图片")
    for image in images:
        if (not isinstance(image, dict) or not isinstance(image.get("media_type"), str)
                or image["media_type"] not in IMAGE_TYPES):
            raise board.BoardError("图片只支持 image/png、image/jpeg、image/webp、image/gif")
        if not isinstance(image.get("data"), str):
            raise board.BoardError("图片 data 必须是 base64 字符串")
        try:
            raw = base64.b64decode(image["data"], validate=True)
        except ValueError as exc:
            raise board.BoardError("图片 data 不是有效的 base64") from exc
        if len(raw) > MAX_IMAGE_BYTES:
            raise board.BoardError("单张图片解码后不能超过 5 MB")
    return images


def child_environment(spec, environ=None, persistent_names=None):
    env = dict(os.environ if environ is None else environ)
    if persistent_names is None:
        persistent_names = set()
        if os.name == "nt":
            import winreg
            for hive, path in ((winreg.HKEY_CURRENT_USER, "Environment"),
                               (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
                with winreg.OpenKey(hive, path) as key:
                    for index in range(winreg.QueryInfoKey(key)[1]):
                        persistent_names.add(winreg.EnumValue(key, index)[0].upper())
    # The selected native profile is configuration, not the parent's session identity.
    env = {name: value for name, value in env.items()
           if not name.upper().startswith("CREW_")
           and (not name.upper().startswith("CLAUDE") or name.upper() in persistent_names
                or name.upper() == 'CLAUDE_CONFIG_DIR')}
    # A human-facing session: "cli" keeps it out of the sdk-* programmatic class and on the
    # user's subscription. Custom values (e.g. the former "crew-web") are billed as API usage.
    # Crew identity is the session's MCP token, never an inherited variable.
    env.update(CLAUDE_CODE_ENTRYPOINT="cli", MSYS_NO_PATHCONV="1")
    return env
