import json
import uuid

from . import runtime_recovery
from . import board, codex_client, codex_visibility, models, runtimes


class ControlMixin:
    def _control(self, current, subtype, **fields):
        identity = str(uuid.uuid4())
        request = {"type": "control_request", "request_id": identity, "request": {"subtype": subtype, **fields}}
        with current.input_lock:
            if current.stop_reason is not None:
                return
            current.control_requests[identity] = subtype
            current.process.stdin.write(json.dumps(request) + "\n")
            current.process.stdin.flush()

    def _control_response(self, current, response):
        with current.input_lock:
            subtype = current.control_requests.pop(response["request_id"], None)
            if subtype == "get_settings" and response["subtype"] == "success":
                applied = response["response"]["applied"]
                current.applied = {"model": applied["model"], "effort": applied["effort"]}
                current.journal.changed()
            elif subtype == "get_context_usage" and response["subtype"] == "success":
                usage = response["response"]
                current.context = {"used": usage["totalTokens"], "max": usage["maxTokens"], "source": "live"}
                native_id = current.native_id if current.execution else current.journal.path.stem
                current.journal.remember_context(current.runtime, native_id, current.context)
        if subtype in ("set_model", "apply_flag_settings") and response["subtype"] == "success":
            self._control(current, "get_settings")

    def sync_native_titles(self, conn, session_id):
        session = board.session(conn, session_id)
        warnings, handled = [], set()
        with self.lock:
            for current in self.processes.values():
                if (getattr(current, 'runtime', None) == 'codex' and getattr(current, 'session_id', None) == session_id
                        and current.process.poll() is None and not current.client.closed):
                    warning = current.sync_title()
                    if warning:
                        warnings.append(warning)
                    handled.add(current.thread_id)
        pending = [native_id for native_id in codex_visibility.stored_person_threads(conn, session) if native_id not in handled]
        if pending:
            try:
                with codex_client.Client(process_factory=self.process_factory) as client:
                    for native_id in pending:
                        codex_visibility.set_title(client, native_id, session)
            except (codex_client.CodexError, OSError, ValueError) as exc:
                warnings.append('Codex 标题同步失败：' + str(exc))
        return warnings

    def set_options(self, conn, session_id, changes, now=None):
        with self.lock:
            if not changes:
                raise ValueError("至少提供 model、effort 或 speed")
            runtime = runtimes.get(conn, session_id)["runtime"]
            if runtime == "codex":
                options = {**models.get(conn, session_id), **changes}
                models.validate_options(**options, runtime=runtime)
                if self.running(session_id):
                    self.processes[session_id].set_options(options)
                models.set(conn, session_id, **options, now=now)
                return options
            if changes.get("speed") is not None:
                raise ValueError("Claude Code 不支持速度设置；速度只适用于 Codex 会话")
            if "effort" in changes and changes["effort"] is None and self.running(session_id):
                raise ValueError("运行中的会话不能将 effort 恢复默认；请选择具体档位，或停止后设为 null 以在下次启动跟随账号设置")
            options = {**models.get(conn, session_id), **changes}
            if changes.get("model") == models.HAIKU and "effort" not in changes:
                options["effort"] = None
            if "model" not in changes and options["model"] is None and self.running(session_id):
                applied = self.applied(session_id)
                if applied is not None and applied["model"] == models.HAIKU:
                    models.validate_options(applied["model"], options["effort"])
            models.validate_options(**options)
            models.set(conn, session_id, **options, now=now)
            if not self.running(session_id):
                return options
            current = self.processes[session_id]
            if "model" in changes:
                self._control(current, "set_model", **({"model": options["model"]} if options["model"] is not None else {}))
            if "effort" in changes:
                self._control(current, "apply_flag_settings", settings={"effortLevel": options["effort"]})
            return options

    def interrupt(self, session_id):
        with self.lock:
            current = self.processes.get(session_id)
            if current is None:
                raise board.BoardError("网页服务没有管理这个会话进程")
            if not self.running(session_id):
                raise board.BoardError("会话没有在运行")
            if not self.busy(session_id):
                raise board.Conflict("会话当前空闲，没有正在进行的轮次")
            runtime_recovery.record_interrupt(current, session_id)
            if getattr(current, "runtime", "claude") == "codex":
                current.interrupt()
                return {"ok": True}
            request = {"type": "control_request", "request_id": str(uuid.uuid4()), "request": {"subtype": "interrupt"}}
            with current.input_lock:
                current.process.stdin.write(json.dumps(request) + "\n")
                current.process.stdin.flush()
        return {"ok": True}
