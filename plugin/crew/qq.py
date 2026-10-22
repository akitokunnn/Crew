"""QQ official bot transport: one outbound WebSocket for events, HTTPS for sending. Stdlib only.

Rules come from https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html
(single-chat proactive messages are unconditional, 1000 per user per day; the user can turn
them off in the client). Credentials are passed in by the caller and never logged.

Contract used by notifications.Inbox (interface skeleton; the transport task fills it in):

on_event("message", {
    "message_id": str,          # event d.id; the same id may be delivered more than once
    "openid": str,              # d.author.user_openid
    "text": str,                # d.content stripped, "" when absent
    "quoted_ref": str | None,   # REFIDX_... from "ref_msg_idx=" in d.message_scene.ext
    "attachments": [{"kind": "image" | "voice" | "video" | "file", "url": str,
                     "content_type": str, "filename": str | None, "size": int | None,
                     "voice_text": str | None}],   # voice_text = asr_refer_text
})
on_event("interaction", {
    "interaction_id": str, "openid": str, "button_id": str, "data": str,
})
on_state(state, detail)  # state: "connecting" | "connected" | "reconnecting" | "stopped"; detail: str | None
"""


class QQError(RuntimeError):
    """kind: "rejected" (40054013, the user refuses bot messages), "too_long" (40054007),
    "rate_limited" (40034100), "auth" (token / credentials), "fatal" (close codes that forbid
    reconnecting, e.g. bot banned), "api" (any other platform error), "network"."""

    def __init__(self, message, *, kind, status=None, code=None):
        super().__init__(message)
        self.kind, self.status, self.code = kind, status, code


class Client:
    def __init__(self, app_id, app_secret):
        raise NotImplementedError

    def run(self, on_event, on_state, stop):
        """Block until `stop` (threading.Event) is set: connect, identify, heartbeat, resume or
        re-identify after drops. Raises QQError(kind="fatal" | "auth") when reconnecting cannot help."""
        raise NotImplementedError

    def send_text(self, openid, text):
        """Proactive single-chat text. Returns {"id": str, "ref_idx": str | None}."""
        raise NotImplementedError

    def send_markdown(self, openid, markdown, keyboard=None):
        """Proactive single-chat markdown, optionally with a custom keyboard. Same return shape."""
        raise NotImplementedError

    def ack_interaction(self, interaction_id):
        """PUT /interactions/{id} with code 0, so the button stops spinning."""
        raise NotImplementedError

    def download(self, url, limit_bytes):
        """Attachment bytes; QQError(kind="api") when larger than limit_bytes."""
        raise NotImplementedError
