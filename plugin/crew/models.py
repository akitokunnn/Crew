"""Runtime-specific model/effort validation and per-session options."""
import re

from . import runtimes, store


# Model directories come from runtime_catalog; this legacy export remains empty.
MODELS = []
# Legacy callers may display these examples; discovery and native configuration
# determine supported values, rather than this list validating local capability.
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
CODEX_EFFORTS = ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]
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
        if not isinstance(model, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", model) is None:
            raise ValueError("不支持的 Codex 模型: " + str(model))
        if catalog is not None and _choice(model, catalog) is None:
            raise ValueError("Codex 模型不在当前运行时清单中: " + model)
    elif model is not None and (not isinstance(model, str) or
                               re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", model) is None):
        raise ValueError("模型标识格式不正确: " + str(model))
    return model


def validate_effort(effort, runtime="claude", catalog=None, model=None):
    runtimes.validate(runtime)
    if effort is not None and (not isinstance(effort, str) or not effort.strip() or effort != effort.strip()
                               or len(effort) > 100 or effort.startswith('-')
                               or any(ord(char) < 32 or ord(char) == 127 for char in effort)):
        raise ValueError('effort 必须是原生标识格式的非空文本或 null')
    allowed = None
    choice = _choice(model, catalog)
    if choice is not None and ("supportedReasoningEfforts" in choice or "efforts" in choice):
        supported = choice.get("efforts") if "efforts" in choice else choice["supportedReasoningEfforts"]
        if not isinstance(supported, list):
            raise ValueError("Codex effort 清单格式不正确")
        allowed = [item.get("reasoningEffort") if isinstance(item, dict) else item for item in supported]
        if any(not isinstance(value, str) or not value.strip() for value in allowed):
            raise ValueError("Codex effort 清单格式不正确")
    if effort is not None and allowed is not None and effort not in allowed:
        raise ValueError("不支持的 effort: " + str(effort))
    return effort


def validate_options(model, effort, runtime="claude", catalog=None):
    validate(model, runtime=runtime, catalog=catalog)
    validate_effort(effort, runtime=runtime, catalog=catalog, model=model)


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
    current=runtimes.get(conn,session_id)
    conn.execute('INSERT INTO session_runtime_bindings(session_id,runtime,native_id,model,effort,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(session_id,runtime) DO UPDATE SET model=excluded.model,effort=excluded.effort,updated_at=excluded.updated_at',
                 (session_id,runtime,current['native_id'] if current['runtime']==runtime else None,model,effort,store.now_iso() if now is None else now))
