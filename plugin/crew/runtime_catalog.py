"""Discover execution tools without starting processes on the state poll path."""
import json
import os
import shutil
import subprocess
import threading
import time

from . import codex_client, models, tool_updates

NAMES = {"claude": "Claude Code", "codex": "Codex"}
# Each tool's own read-only sign-in report; neither starts a model session.
LOGIN_STATUS = {"claude": ("auth", "status"), "codex": ("login", "status")}


def _login(runtime, result):
    output = (result.stdout + result.stderr).strip()
    if runtime == "claude":
        try:
            report = json.loads(result.stdout)
        except ValueError:
            raise ValueError("claude auth status 没有返回可读的结果：" + (output or "退出码 %d" % result.returncode))
        if not isinstance(report, dict) or not isinstance(report.get("loggedIn"), bool):
            raise ValueError("claude auth status 的结果缺少 loggedIn：" + output)
        return report["loggedIn"], ("登录方式 " + report["authMethod"]) if report["loggedIn"] and report.get("authMethod") else None
    if result.returncode == 0:
        # Codex prints its sign-in method on stderr, after any configuration warnings.
        return True, output.splitlines()[-1] if output else None
    if "Not logged in" in output:
        return False, None
    raise ValueError("codex login status 失败：" + (output or "退出码 %d" % result.returncode))


def readiness(which=shutil.which, run=tool_updates._run, inspector=None):
    """Installed and signed-in state of each execution tool, for the first-run check."""
    inspector = inspector or tool_updates.Inspector(which=which, run=run)
    report = []
    for runtime in ("claude", "codex"):
        entry = {"id": runtime, "name": NAMES[runtime], "installed": False, "logged_in": None, "detail": None, "error": None}
        report.append(entry)
        try:
            record = inspector.installation(runtime)
        except (OSError, ValueError, codex_client.CodexError) as exc:
            entry.update(installed=True, error="安装路径识别失败：" + str(exc))
            continue
        if record["installation"] == "missing":
            continue
        entry["installed"] = True
        if not record["_command"]:
            entry["error"] = record["error"] or "未能识别此工具的可执行文件，无法检测登录状态"
            continue
        try:
            result = run([*record["_command"], *LOGIN_STATUS[runtime]], timeout=20,
                         env={**os.environ, "DISABLE_AUTOUPDATER": "1"})
            entry["logged_in"], entry["detail"] = _login(runtime, result)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            entry["error"] = "登录状态检测失败：" + str(exc)
    return report


class Catalog:
    def __init__(self, client_factory=codex_client.Client, which=shutil.which):
        self.client_factory, self.which = client_factory, which
        self.lock = threading.RLock()
        self.updated_at = None
        self.codex = {"id": "codex", "name": "Codex", "available": bool(which("codex")),
                      "error": None if which("codex") else "尚未安装 Codex CLI",
                      "models": [], "models_source": "discovered", "efforts": ["low", "medium", "high", "xhigh"]}

    def snapshot(self):
        """``models_source``: Codex reports its own full list (model/list); Claude Code has no model list to ask,
        so its entry carries Crew's preset of common models, and any other Claude model ID is just as usable."""
        with self.lock:
            return [{"id": "claude", "name": "Claude Code", "available": bool(self.which("claude")),
                     "error": None if self.which("claude") else "尚未安装 Claude Code CLI",
                     "models": models.MODELS, "models_source": "preset", "efforts": models.EFFORTS},
                    {**self.codex, "models": [dict(item) for item in self.codex["models"]]}]

    def refresh(self, force=False):
        with self.lock:
            if not force and self.updated_at is not None and time.monotonic() - self.updated_at < 300:
                return self.snapshot()
            if not self.which("codex"):
                self.codex.update(available=False, error="尚未安装 Codex CLI", models=[])
            else:
                try:
                    with self.client_factory(timeout=15) as client:
                        entries, cursor = [], None
                        while True:
                            page = client.list_models(**({"cursor": cursor} if cursor else {}))
                            for item in page["data"]:
                                efforts = [choice["reasoningEffort"] for choice in item.get("supportedReasoningEfforts", [])]
                                # Speeds are Codex service tiers; standard speed ("default") is always available.
                                speeds = [{"id": tier["id"], "name": tier.get("name") or tier["id"],
                                           "description": tier.get("description") or ""}
                                          for tier in item.get("serviceTiers") or []]
                                entries.append({"id": item.get("model") or item["id"], "name": item.get("displayName") or item["id"],
                                                "efforts": efforts, "default_effort": item.get("defaultReasoningEffort"),
                                                "speeds": speeds, "default_speed": item.get("defaultServiceTier"),
                                                "context_window": item.get("contextWindow")})
                            cursor = page.get("nextCursor")
                            if not cursor:
                                break
                    available_efforts = list(dict.fromkeys(effort for item in entries for effort in item['efforts']))
                    self.codex.update(available=True, error=None, models=entries, efforts=available_efforts)
                except (OSError, ValueError, KeyError, TypeError, codex_client.CodexError) as exc:
                    self.codex.update(available=False, error="Codex 模型清单读取失败：" + str(exc))
            self.updated_at = time.monotonic()
            return self.snapshot()
