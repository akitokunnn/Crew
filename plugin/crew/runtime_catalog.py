"""Discover execution tools without starting processes on the state poll path."""
import shutil
import threading
import time

from . import codex_client, models


class Catalog:
    def __init__(self, client_factory=codex_client.Client, which=shutil.which):
        self.client_factory, self.which = client_factory, which
        self.lock = threading.RLock()
        self.updated_at = None
        self.codex = {"id": "codex", "name": "Codex", "available": bool(which("codex")),
                      "error": None if which("codex") else "尚未安装 Codex CLI",
                      "models": [], "efforts": ["low", "medium", "high", "xhigh"]}

    def snapshot(self):
        with self.lock:
            return [{"id": "claude", "name": "Claude Code", "available": bool(self.which("claude")),
                     "error": None if self.which("claude") else "尚未安装 Claude Code CLI",
                     "models": models.MODELS, "efforts": models.EFFORTS},
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
                                entries.append({"id": item.get("model") or item["id"], "name": item.get("displayName") or item["id"],
                                                "efforts": efforts, "default_effort": item.get("defaultReasoningEffort"),
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
