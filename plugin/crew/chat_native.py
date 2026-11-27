from contextlib import closing
import json
import sqlite3
import subprocess
import threading

from . import behavior_prompts, runtime_recovery
from . import board, claude_limits, codex_client, goal_messages, goals, runtime_switch, runtimes, store
from .chat_base import (
    CLAUDE_NO_CONVERSATION, NATIVE_MISSING, NATIVE_REPLACED, NATIVE_LOST_INPUT, track_background,
    native_output, lost_turn,
)


class NativeMixin:
    def _read(self, session_id, current):
        code = None
        try:
            current.journal.bind_claude_subagents(getattr(current, 'native_id', session_id))
            for line in current.process.stdout:
                try:
                    record = json.loads(line)
                    if (record.get('type') == 'user' and isinstance(record.get('uuid'), str)
                            and record.get('session_id') == (current.native_id if current.execution else session_id)
                            and record.get('message', {}).get('role') == 'user' and not record.get('parent_tool_use_id')):
                        with closing(store.connect(self.home)) as prompt_conn:
                            behavior_prompts.delivered(prompt_conn, behavior_prompts.input_id(session_id, record['uuid']),
                                confirmed=True, evidence='Claude 原生 user 回执确认同一输入 ID：' + record['uuid'])
                    if (self.processes.get(session_id) is current and record.get('session_id') == (current.native_id if current.execution else session_id)
                            and not record.get('parent_tool_use_id')):
                        known_native = (record.get('type')=='assistant' or
                            record.get('type')=='system' and record.get('subtype')=='init')
                        receipt=current.pending_native_accept
                        acknowledged=(record.get('type')=='user' and receipt is not None and record.get('uuid')==receipt[0]
                                      and record.get('message',{}).get('role')=='user')
                        if known_native and current.replacing is not None:
                            current.journal.emit('native_replaced', runtime='claude', native_id=record['session_id'],
                                                 text=NATIVE_REPLACED % current.replacing['lost_native'],
                                                 **current.replacing)
                            current.replacing = None
                        if known_native and not current.native_binding_confirmed or acknowledged:
                            try:
                                with closing(store.connect(self.home)) as native_conn:
                                    if current.execution:
                                        current.native_binding_confirmed = True
                                    elif runtimes.get(native_conn,session_id)['runtime']=='claude':
                                        if runtimes.get(native_conn,session_id)['native_id'] is None:
                                            runtimes.set(native_conn,session_id,'claude',session_id)
                                        current.native_binding_confirmed=True
                                        if acknowledged:
                                            runtime_switch.accepted(native_conn,session_id,receipt[0],receipt[1])
                                            current.pending_native_accept=None
                            except sqlite3.Error as exc:
                                # Keep draining native output and retain the handoff for
                                # the next accepted input rather than losing the reader.
                                current.journal.emit('error',text='原生会话确认记录失败，交接会保留重试：'+str(exc))
                    if (current.execution and record.get('type') == 'user' and record.get('session_id') == current.native_id
                            and record.get('message', {}).get('role') == 'user' and not record.get('parent_tool_use_id')
                            and record.get('uuid') in current.goal_manifests):
                        from . import goal_context
                        with closing(store.connect(self.home)) as goal_conn:
                            goal_context.mark_applied(goal_conn, current.goal_manifests.pop(record['uuid']),
                                                      execution_id=current.execution['id'])
                    track_background(current, record)
                    if record["type"] == "control_response":
                        self._control_response(current, record["response"])
                    if record["type"] == 'system' and record.get('subtype') == 'compact_boundary' and not record.get('parent_tool_use_id'):
                        current.trust_context = None
                    if record["type"] == "rate_limit_event":
                        try:
                            claude_limits.capture(self.home, record)
                        except OSError as exc:
                            current.journal.emit("error", text="Claude 额度记录失败：" + str(exc))
                    if not record.get("parent_tool_use_id") and (record["type"] == "result" or
                            record["type"] == "system" and record.get("subtype") == "compact_boundary"):
                        self._control(current, "get_context_usage")
                    if record["type"] == "result" and not record.get("parent_tool_use_id"):
                        with current.input_lock:
                            current.journal.consume(record, interrupted=current.interrupt_requested)
                            if (record.get("is_error") and current.resumed_native is not None
                                    and CLAUDE_NO_CONVERSATION + current.resumed_native in
                                    record.get("errors", []) + [record.get("result")]):
                                current.journal.emit("error", text=NATIVE_MISSING % current.resumed_native,
                                                     native_missing=current.resumed_native, runtime="claude")
                                if current.maintenance is None:
                                    current.native_lost = current.resumed_native
                            if current.pending_compact and current.stop_reason is None and current.process.poll() is None:
                                current.pending_compact = False
                                self._send(session_id, "/compact")
                        if (current.execution is not None and current.maintenance is None
                                and not record.get("is_error")):
                            goal_messages.return_turn(self.home, current.execution['id'], current.journal)
                    else:
                        current.journal.consume(record)
                except (ValueError, KeyError, TypeError) as exc:
                    current.journal.emit("error", text="Claude 协议错误：" + str(exc))
            code = current.process.wait()
            current.stderr_reader.join(timeout=1)
            with current.input_lock:
                if code and current.stop_reason is None:
                    current.journal.emit("error", text="Claude 退出（%s）：%s" % (code, "\n".join(current.stderr)))
                elif not code and current.stop_reason is None and current.journal.status == "working":
                    current.journal.emit("error", text="Claude 会话连接已结束，本轮未完成")
        except (OSError, ValueError) as exc:
            current.journal.emit("error", text=str(exc))
        finally:
            try:
                with current.input_lock:
                    runtime_recovery.record_exit(current, session_id)
                    if current.journal.status == "working":
                        current.journal.emit("turn_result", status="interrupted" if (
                            current.stop_reason or current.interrupt_requested) else "failed",
                                             success=False, reason=current.stop_reason or (
                                                 'user_interrupt' if current.interrupt_requested else 'process_exit'), exit_code=code)
                    if current.pending_compact:
                        current.pending_compact = False
                        current.journal.emit("compact_cancelled", reason="会话进程已退出，取消这一轮结束后的压缩")
                    current.journal.set_status("offline")
            finally:
                current.reader_done.set()
        if current.native_lost is not None and current.stop_reason is None:
            threading.Thread(target=self._replace_lost_native, args=(session_id, current),
                             name="crew-native-replace", daemon=True).start()

    def _replace_lost_native(self, key, current):
        """Open a new native for a scope whose Claude conversation no longer exists.

        The general conversation restarts under its own Crew ID (Claude has no
        conversation by that ID); a goal execution continues in its next
        generation. Either way the unanswered input is handed over with the
        task background, and the new session announces itself as new.
        """
        with current.journal.condition:
            current.journal.refresh()
            events = list(current.journal.events)
        marker = next(event for event in reversed(events) if event.get('native_missing') == current.native_lost)
        identity = NATIVE_LOST_INPUT + key + ':' + str(marker['seq'])
        text = lost_turn(events)
        display = 'Crew：原会话丢失，已新开原生会话并交接任务背景。'
        try:
            if current.execution is None:
                with closing(store.connect(self.home)) as conn:
                    session = board.session(conn, key)
                self.send(session, text, source='crew', identity=identity, display_text=display)
            else:
                self.goal_controller.replace_lost(current.execution['id'], current.native_lost, text,
                                                  identity=identity, display_text=display)
        except (board.BoardError, codex_client.CodexError, OSError, subprocess.SubprocessError) as exc:
            current.journal.emit('error', text='原会话丢失后未能新开会话（下次输入时会再新开并交接）：%s: %s'
                                 % (type(exc).__name__, exc))

    def _stderr(self, current):
        for line in current.process.stderr:
            current.stderr.append(line.rstrip())

    def _external_agents(self):
        # cli.claude_agents already validates the shape; only its failure needs translating.
        try:
            return self.agents_provider()
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
            raise board.Conflict("进程状态未知，请稍后再试：" + str(exc)) from exc

    def _runs_in_claude(self, session_id):
        with closing(store.connect(self.home)) as conn:
            return runtimes.get(conn, session_id)["runtime"] == "claude"

    def _check_offline(self, session):
        session_id = session["session_id"]
        if self.running(session_id):
            if not self._runs_in_claude(session_id):
                raise board.Conflict('它在后台在线，请用 Crew 工具 `message_send` 发消息，`to` 填 ' + session['crew_name'])
            raise board.Conflict("它在后台在线，直接用 SendMessage 发给 " + session["crew_name"])
        with closing(store.connect(self.home)) as conn:
            runtime = runtimes.get(conn, session_id)["runtime"]
            prior_claude=conn.execute("SELECT 1 FROM session_runtime_bindings WHERE session_id=? AND runtime='claude' AND native_id IS NOT NULL",(session_id,)).fetchone()
            prior_codex=conn.execute("SELECT 1 FROM session_runtime_bindings WHERE session_id=? AND runtime='codex' AND native_id IS NOT NULL",(session_id,)).fetchone()
        for agent in ([] if runtime == "codex" and not prior_claude else self._external_agents()):
            if agent["sessionId"] == session_id:
                if runtime=='claude':
                    raise board.Conflict("它在 VS Code 里在线，直接用 SendMessage 发给 " + agent["name"])
                raise board.Conflict("它的 Claude 会话在 VS Code 或终端里在线，请先关闭该外部会话")
        previous = self.processes.get(session_id)
        if previous is not None and not previous.reader_done.wait(10):
            raise board.BoardError("上一轮会话进程的输出尚未结束，请稍后查看状态")
        if runtime=='claude' and prior_codex:
            from .codex_runtime import RuntimeLease
            lease=RuntimeLease(self.home,session_id).acquire()
            lease.release()

    def missing_native(self, conn, session):
        """The Claude native ID this scope would resume, when Claude already said it has none.

        A confirmed replacement or any native output after that report clears it.
        """
        execution = session.get('_execution')
        if execution is None:
            binding = runtimes.get(conn, session['session_id'])
        else:
            execution = goals.get_execution(conn, execution['id'])
            binding = next(({'runtime': item['runtime'], 'native_id': item['native_id']}
                            for item in execution['bindings'] if item['runtime'] == execution['runtime']),
                           {'runtime': execution['runtime'], 'native_id': None})
        if binding['runtime'] != 'claude' or binding['native_id'] is None:
            return None
        journal = self.journal(session.get('_storage_id', session['session_id']))
        with journal.condition:
            journal.refresh()
            for event in reversed(journal.events):
                if native_output(event) or event['type'] == 'native_replaced':
                    return None
                if event['type'] == 'error' and event.get('native_missing') == binding['native_id']:
                    return binding['native_id']
        return None
