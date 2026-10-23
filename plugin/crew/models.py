"""Supported model and effort choices and per-session options."""
from . import store


MODELS = [
    {"id": "claude-opus-5-5", "name": "Opus 5.5", "context_window": 1000000},
    {"id": "claude-sonnet-5-5", "name": "Sonnet 5.5", "context_window": 1000000},
    {"id": "claude-fable-5-1", "name": "Fable 5.1", "context_window": 1000000},
    {"id": "claude-haiku-4-5-20251001", "name": "Haiku 4.5", "context_window": 200000},
]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
HAIKU = "claude-haiku-4-5-20251001"
INHERIT = object()


def validate(model):
    if model is not None and model not in [choice["id"] for choice in MODELS]:
        raise ValueError("不支持的模型: " + str(model))
    return model


def validate_effort(effort):
    if effort is not None and effort not in EFFORTS:
        raise ValueError("不支持的 effort: " + str(effort))
    return effort


def validate_options(model, effort):
    validate(model)
    validate_effort(effort)
    if model == HAIKU and effort is not None:
        raise ValueError("Haiku 4.5 不支持 effort，请将 effort 设为 null")


def get(conn, session_id):
    row = conn.execute("SELECT model,effort FROM session_options WHERE session_id=?", (session_id,)).fetchone()
    return dict(row) if row is not None else {"model": None, "effort": None}


def set(conn, session_id, model, effort, now=None):
    validate_options(model, effort)
    if model is None and effort is None:
        conn.execute("DELETE FROM session_options WHERE session_id=?", (session_id,))
    else:
        conn.execute("INSERT INTO session_options(session_id,model,effort,set_at) VALUES(?,?,?,?) "
                     "ON CONFLICT(session_id) DO UPDATE SET model=excluded.model,effort=excluded.effort,set_at=excluded.set_at",
                     (session_id, model, effort, store.now_iso() if now is None else now))
