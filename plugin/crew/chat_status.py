from contextlib import closing
from pathlib import Path

from . import board, runtimes, store, subagents
from .chat_base import IMAGE_TYPES
from .chat_journal import Journal


class StatusMixin:
    def managed_pids(self):
        with self.lock:
            return [current.process.pid for current in self.processes.values() if current.process.poll() is None]

    def path(self, session_id):
        return self.home / "chat" / (session_id + ".jsonl")

    def journal(self, session_id):
        with self.lock:
            if session_id not in self.journals:
                self.journals[session_id] = Journal(self.path(session_id))
            else:
                self.journals[session_id].refresh()
            return self.journals[session_id]

    def file(self, session_id, name):
        if not isinstance(name, str) or not name or Path(name).name != name or "/" in name or "\\" in name:
            raise board.BoardError("图片文件名不能包含路径")
        journal = self.journal(session_id)
        with journal.condition:
            if not any(name in event.get("images", []) for event in journal.events if event["type"] == "message"):
                raise board.BoardError("该图片不在会话聊天记录中")
        media_type = next(media for media, suffix in IMAGE_TYPES.items() if name.endswith(suffix))
        return self.home / "chat" / "files" / session_id / name, media_type

    def running(self, session_id):
        current = self.processes.get(session_id)
        return current is not None and current.process.poll() is None

    def is_owned(self, session_id):
        return self.running(session_id)

    def owns_pid(self, session_id, pid):
        with self.lock:
            current=self.processes.get(session_id)
            return (current is not None and current.process.poll() is None
                    and self.processes.get(session_id) is current and current.process.pid==pid)

    def busy(self, session_id):
        # A maintenance-held native counts as working for every other caller.
        return self.running(session_id) and (self.journal(session_id).status == "working"
                                             or getattr(self.processes.get(session_id), "maintenance", None) is not None)

    def scope_busy(self, key, runtime, native_id):
        """Check another host's ownership too, without starting a native process."""
        if self.running(key):
            return self.busy(key)
        if runtime == 'claude':
            return native_id is not None and any(agent['sessionId'] == native_id and agent.get('status') != 'idle'
                                                for agent in self._external_agents())
        journal = self.journal(key)
        with journal.condition:
            journal.refresh()
            if journal.status != 'working':
                return False
        from .codex_runtime import RuntimeLease
        try:
            lease = RuntimeLease(self.home, key).acquire()
            lease.release()
        except board.Conflict:
            return True
        except (OSError, ValueError) as exc:
            raise board.Conflict('无法确认另一个宿主的原生执行已退出，未启动新执行：' + str(exc)) from exc
        return False

    def maintenance_held(self, identity):
        """Hold business input across a person's contexts, including borrowed idle natives."""
        # Input readers can check this while another person's maintenance exits.
        for key, current in list(self.processes.items()):
            if getattr(current, 'maintenance', None) is None:
                continue
            logical = current.execution['session_id'] if current.execution is not None else key
            if identity in (key, logical) and current.process.poll() is None:
                return True
        return False

    def activity(self, session_id):
        with self.lock:
            if not self.busy(session_id):
                return None
            journal = self.journal(session_id)
            with journal.condition:
                return dict(journal.progress) if journal.progress is not None else None

    def applied(self, session_id):
        with self.lock:
            # A finished process keeps its last reading; options changed while offline must not show stale values.
            current = self.processes.get(session_id)
            if current is None or not self.running(session_id):
                return None
            with current.input_lock:
                return dict(current.applied) if current.applied is not None else None

    def context(self, session_id):
        with self.lock:
            current = self.processes.get(session_id)
            if current is None or not self.running(session_id):
                return None
            with current.input_lock:
                return dict(current.context) if current.context is not None else None

    def display_context(self, session_id, conn=None, *, binding=None):
        """Keep the last reading for display while the bound conversation is offline."""
        live = self.context(session_id)
        if live is not None:
            return live
        if conn is None:
            with closing(store.connect(self.home)) as conn:
                return self.display_context(session_id, conn, binding=binding)
        if binding is None:
            binding = conn.execute(
                "SELECT e.runtime, b.native_id FROM goal_executions e LEFT JOIN goal_execution_bindings b "
                "ON b.execution_id=e.id AND b.runtime=e.runtime WHERE e.id=?", (session_id,)).fetchone()
            if binding is None:
                binding = runtimes.get(conn, session_id)
        runtime, native_id = binding['runtime'], binding['native_id']
        if native_id is None:
            return None
        with self.lock:
            journal = self.journals.get(session_id)
            if journal is not None:
                with journal.condition:
                    saved = journal.last_context
                    if saved is not None and saved[:2] == (runtime, native_id):
                        return dict(saved[2])
        if self.context_history is not None:
            return self.context_history(conn, runtime, native_id)
        return None

    def context_state(self, session_id, conn=None):
        """Read native usage and compaction for one general or task execution."""
        with self.lock:
            current = self.processes.get(session_id)
            phase = None
            if current is not None and self.running(session_id):
                with current.input_lock:
                    if (current.journal.progress or {}).get('phase') == 'compacting':
                        phase = 'compacting'
                    elif current.pending_compact:
                        phase = 'queued'
        return {'context': self.display_context(session_id, conn), 'compaction': phase}

    def history(self, session_id):
        journal = self.journal(session_id)
        with journal.condition:
            running = self.running(session_id)
            return {"session_id": session_id, "events": list(journal.events), "running": running,
                    "busy": running and journal.status == "working"}

    def subagent_tasks(self, session_id):
        if not self.path(session_id).exists():
            return []
        journal = self.journal(session_id)
        with journal.condition:
            current = self.processes.get(session_id)
            runtime = getattr(current, 'runtime', None)
            active_root = ((getattr(current, 'thread_id', None) if runtime == 'codex' else
                            getattr(current, 'native_id', session_id)) if self.running(session_id) else None)
            return subagents.snapshot(journal.subagents, active_root, runtime)
