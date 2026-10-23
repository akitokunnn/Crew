"""Runtime-specific model/effort validation and per-session options."""
import re

from . import runtimes, store


MODELS = [
    {"id": "claude-opus-5-5", "name": "Opus 5.5", "context_window": 1000000},
    {"id": "claude-sonnet-5-5", "name": "Sonnet 5.5", "context_window": 1000000},
    {"id": "claude-fable-5-1", "name": "Fable 5.1", "context_window": 1000000},
    {"id": "claude-haiku-4-5-20251001", "name": "Haiku 4.5", "context_window": 200000},
]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
CODEX_EFFORTS = ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
HAIKU = "claude-haiku-4-5-20251001"
INHERIT = object()


def _catalog(catalog):
    if catalog is None:
        return None
    choices = catalog.get("data") if isinstance(catalog, dict) else catalog
    if not isinstance(choices, list) or any(not isinstance(choice, dict) for choice in choices):
        raise ValueError("Codex 模型清单必须是 model/list 返回的 data 数组")
    return choices


def _choice(model, catalog):
    choices = _catalog(catalog)
    if choices is None:
        return None
    if model is None:
        return next((choice for choice in choices if choice.get("isDefault") or choice.get("is_default")), None)
    return next((choice for choice in choices if model in (choice.get("id"), choice.get("model"))), None)


def validate(model, runtime="claude", catalog=None):
    runtimes.validate(runtime)
    if runtime == "codex":
        if model is None:
            return None
        if (not isinstance(model, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", model) is None
                or model.lower().startswith("claude") or model.lower() in ("opus", "sonnet", "haiku")):
            raise ValueError("不支持的 Codex 模型: " + str(model))
        if catalog is not None and _choice(model, catalog) is None:
            raise ValueError("Codex 模型不在当前运行时清单中: " + model)
    elif model is not None and model not in [choice["id"] for choice in MODELS]:
        raise ValueError("不支持的模型: " + str(model))
    return model


def validate_effort(effort, runtime="claude", catalog=None, model=None):
    runtimes.validate(runtime)
    allowed = EFFORTS if runtime == "claude" else CODEX_EFFORTS
    choice = _choice(model, catalog) if runtime == "codex" else None
    if choice is not None and ("supportedReasoningEfforts" in choice or "efforts" in choice):
        supported = choice.get("efforts") if "efforts" in choice else choice["supportedReasoningEfforts"]
        if not isinstance(supported, list):
            raise ValueError("Codex effort 清单格式不正确")
        allowed = [item.get("reasoningEffort") if isinstance(item, dict) else item for item in supported]
        if any(not isinstance(value, str) or not value.strip() for value in allowed):
            raise ValueError("Codex effort 清单格式不正确")
    if effort is not None and effort not in allowed:
        raise ValueError("不支持的 effort: " + str(effort))
    return effort


def validate_options(model, effort, runtime="claude", catalog=None):
    validate(model, runtime=runtime, catalog=catalog)
    validate_effort(effort, runtime=runtime, catalog=catalog, model=model)
    if runtime == "claude" and model == HAIKU and effort is not None:
        raise ValueError("Haiku 4.5 不支持 effort，请将 effort 设为 null")


def get(conn, session_id):
    row = conn.execute("SELECT model,effort FROM session_options WHERE session_id=?", (session_id,)).fetchone()
    return dict(row) if row is not None else {"model": None, "effort": None}


def set(conn, session_id, model, effort, now=None, runtime=None, catalog=None):
    runtime = runtimes.get(conn, session_id)["runtime"] if runtime is None else runtime
    validate_options(model, effort, runtime=runtime, catalog=catalog)
    if model is None and effort is None:
        conn.execute("DELETE FROM session_options WHERE session_id=?", (session_id,))
    else:
        conn.execute("INSERT INTO session_options(session_id,model,effort,set_at) VALUES(?,?,?,?) "
                     "ON CONFLICT(session_id) DO UPDATE SET model=excluded.model,effort=excluded.effort,set_at=excluded.set_at",
                     (session_id, model, effort, store.now_iso() if now is None else now))
