"""Supported model choices and per-session selection."""
from . import store


MODELS = [
    {"id": "claude-opus-5-5", "name": "Opus 5.5"},
    {"id": "claude-sonnet-5-5", "name": "Sonnet 5.5"},
    {"id": "claude-fable-5-1", "name": "Fable 5.1"},
    {"id": "claude-haiku-4-5-20251001", "name": "Haiku 4.5"},
]
INHERIT = object()


def validate(model):
    if model is not None and model not in [choice["id"] for choice in MODELS]:
        raise ValueError("不支持的模型: " + str(model))
    return model


def get(conn, session_id):
    row = conn.execute("SELECT model FROM session_models WHERE session_id=?", (session_id,)).fetchone()
    return row["model"] if row is not None else None


def set(conn, session_id, model, now=None):
    validate(model)
    if model is None:
        conn.execute("DELETE FROM session_models WHERE session_id=?", (session_id,))
    else:
        conn.execute("INSERT INTO session_models(session_id,model,set_at) VALUES(?,?,?) "
                     "ON CONFLICT(session_id) DO UPDATE SET model=excluded.model,set_at=excluded.set_at",
                     (session_id, model, store.now_iso() if now is None else now))
