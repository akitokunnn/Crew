"""Shared execution state and native startup capabilities.

ExecutionStore owns runtime registration and journal identity for both general
and goal scopes. NativeLauncher owns process construction, registration and
reader activation. Neither decides task admission, scheduling or identity
ownership; those checks happen before a caller asks to launch.
"""
from pathlib import Path
import threading
from types import MappingProxyType

from .session_journal import Journal
from . import subagents


class ExecutionStore:
    def __init__(self, home):
        self.home = Path(home)
        self._lock = threading.RLock()
        self._processes = {}
        self._journals = {}
        self._process_view = MappingProxyType(self._processes)
        self._journal_view = MappingProxyType(self._journals)

    @property
    def processes(self):
        return self._process_view

    @property
    def journals(self):
        return self._journal_view

    @property
    def lock(self):
        return self._lock

    def locked(self):
        """Serialize runtime-state operations; this is not a database transaction."""
        return self._lock

    def current(self, key):
        return self._processes.get(key)

    def register(self, key, current):
        with self._lock:
            self._processes[key] = current

    def remove(self, key, current=None):
        with self._lock:
            if current is None or self._processes.get(key) is current:
                return self._processes.pop(key, None)
        return None

    def path(self, key):
        return self.home / 'chat' / (key + '.jsonl')

    def journal(self, key):
        with self._lock:
            if key not in self._journals:
                self._journals[key] = Journal(self.path(key))
            else:
                self._journals[key].refresh()
            return self._journals[key]

    def running(self, key):
        current = self.current(key)
        return current is not None and current.process.poll() is None

    def owns_pid(self, key, pid):
        current = self.current(key)
        if current is None:
            return False
        # Native liveness can run transport code. Let retirement update the
        # registry while it is observed, then verify this is still the owner.
        alive = current.process.poll() is None
        with self._lock:
            return alive and self.current(key) is current and current.process.pid == pid

    def busy(self, key):
        return self.running(key) and (self.journal(key).status == 'working'
                                     or getattr(self.current(key), 'maintenance', None) is not None)

    def maintenance_held(self, identity):
        for key, current in list(self._processes.items()):
            if getattr(current, 'maintenance', None) is None:
                continue
            logical = current.execution['session_id'] if current.execution is not None else key
            if identity in (key, logical) and current.process.poll() is None:
                return True
        return False

    def activity(self, key):
        with self._lock:
            if not self.busy(key):
                return None
            journal = self.journal(key)
            with journal.condition:
                return dict(journal.progress) if journal.progress is not None else None

    def applied(self, key):
        with self._lock:
            current = self.current(key)
            if current is None or not self.running(key):
                return None
            with current.input_lock:
                return dict(current.applied) if current.applied is not None else None

    def context(self, key):
        with self._lock:
            current = self.current(key)
            if current is None or not self.running(key):
                return None
            with current.input_lock:
                return dict(current.context) if current.context is not None else None

    def history(self, key):
        journal = self.journal(key)
        with journal.condition:
            running = self.running(key)
            return {'session_id': key, 'events': list(journal.events), 'running': running,
                    'busy': running and journal.status == 'working'}

    def subagent_tasks(self, key):
        if not self.path(key).exists():
            return []
        journal = self.journal(key)
        with journal.condition:
            current = self.current(key)
            runtime = getattr(current, 'runtime', None)
            active_root = ((getattr(current, 'thread_id', None) if runtime == 'codex' else
                            getattr(current, 'native_id', key)) if self.running(key) else None)
            return subagents.snapshot(journal.subagents, active_root, runtime)


class NativeLauncher:
    def __init__(self, executions, plugin_root, *, process_factory, send_message, control, read, stderr):
        self.executions = executions
        self.home, self.plugin_root = executions.home, Path(plugin_root)
        self.process_factory = process_factory
        self._send_message = send_message
        self._control, self._read, self._stderr = control, read, stderr

    def codex(self, session, spec, options, *, resume=False, execution=None, maintenance=None, goal_handler=None):
        from .codex_runtime import CodexRunning
        key = execution['id'] if execution is not None else session['session_id']
        current = CodexRunning(self.home, self.plugin_root, session, spec, options,
                               self.executions.journal(key), self._send_message,
                               process_factory=self.process_factory, resume=resume, execution=execution,
                               goal_handler=goal_handler, maintenance=maintenance)
        self.executions.register(key, current)
        return current

    def claude(self, command, *, key, cwd, environment, execution=None, native_id=None,
               maintenance=None, goal_manifests=None, name='crew-chat'):
        from . import install, native_process
        install.ensure(self.home, self.plugin_root)
        current = native_process.launch_claude(command, cwd=cwd, environment=environment,
                                              journal=self.executions.journal(key), process_factory=self.process_factory)
        current.maintenance = maintenance
        if execution is not None:
            current.execution, current.identity_session_id, current.native_id = execution, execution['session_id'], native_id
            current.goal_manifests = dict(goal_manifests or {})
        self.executions.register(key, current)
        current.start_readers(key, control=self._control, read=self._read, stderr=self._stderr, name=name)
        return current
