"""Owned native processes: clean environment, pipe startup and bounded shutdown.

Conversation/goal routing prepares identities and commands. This module owns
OS process settings and reader lifetimes, without importing either router.
Shutdown accepts both Claude and Codex runtime adapters, and drains each
adapter outside the caller's registry lock.
"""
import collections
import os
from pathlib import Path
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

from .claude_events import ClaudeEvents


def child_environment(spec, environ=None, persistent_names=None):
    env = dict(os.environ if environ is None else environ)
    if persistent_names is None:
        persistent_names = set()
        if os.name == "nt":
            import winreg
            for hive, path in ((winreg.HKEY_CURRENT_USER, "Environment"),
                               (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
                with winreg.OpenKey(hive, path) as key:
                    for index in range(winreg.QueryInfoKey(key)[1]):
                        persistent_names.add(winreg.EnumValue(key, index)[0].upper())
    env = {name: value for name, value in env.items()
           if not name.upper().startswith("CREW_")
           and (not name.upper().startswith("CLAUDE") or name.upper() in persistent_names)}
    # VS Code hides sdk-cli transcripts; identify this human-facing host explicitly.
    env.update(CLAUDE_CODE_ENTRYPOINT="crew-web", CREW_NAME=spec["name"], CREW_ROLE=spec["role"], CREW_SPAWN_ID=spec["id"],
               MSYS_NO_PATHCONV="1")
    if spec["group"] is not None:
        env["CREW_GROUP"] = spec["group"]
    return env


def claude_executable():
    exe = shutil.which("claude")
    if exe is None:
        raise FileNotFoundError("claude not found in PATH")
    if Path(exe).suffix.lower() == ".cmd":
        # The local npm shim starts a wrapper cmd.exe; agents reports the native child's PID.
        exe = str(Path(exe).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe")
    return exe


class Running:
    def __init__(self, process, journal):
        self.runtime = "claude"
        self.execution = None
        self.goal_manifests = {}
        self.process, self.journal = process, journal
        self.events = ClaudeEvents(journal)
        # A finished turn can start its queued compact through _send under this same lock.
        self.input_lock = threading.RLock()
        self.control_requests = {}
        self.applied = None
        self.context = None
        self.pending_compact = False
        self.stop_reason = None
        self.stderr = collections.deque(maxlen=20)
        self.reader_done = threading.Event()
        self.pending_native_accept = None
        self.native_binding_confirmed = False
        # Set while department maintenance compacts this native; owned=True means
        # maintenance started it and only the fixed compact command may enter.
        self.maintenance = None


    def start_readers(self, key, *, control, read, stderr, name='crew-chat'):
        """Begin the native handshake and its pipe readers after registration."""
        control(self, 'get_settings')
        control(self, 'get_context_usage')
        self.stderr_reader = threading.Thread(target=stderr, args=(self,), name=name+'-stderr', daemon=True)
        self.stderr_reader.start()
        self.reader = threading.Thread(target=read, args=(key, self), name=name, daemon=True)
        self.reader.start()


def launch_claude(command, *, cwd, environment, journal, process_factory):
    """Start one pipe-based Claude runtime; callers register before readers start."""
    journal.reset_runtime()
    process = process_factory(command, cwd=cwd, env=environment, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                              encoding='utf-8', bufsize=1,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    return Running(process, journal)


def close_runtimes(runtimes):
    """Close independent native adapters concurrently, retaining each real error."""
    closing_processes = list(runtimes)
    def close_current(current):
        if current.process.poll() is None:
            if getattr(current, "runtime", "claude") == "codex":
                with current.input_lock:
                    current.stop_reason = 'service_close'
                current.close()
            else:
                with current.input_lock:
                    current.stop_reason = 'service_close'
                    try:
                        current.process.stdin.close()
                    except OSError:
                        current.stop_reason = None
                        raise
        current.reader.join(timeout=1)

    errors = []
    if closing_processes:
        with ThreadPoolExecutor(max_workers=min(8, len(closing_processes)), thread_name_prefix='crew-native-close') as pool:
            pending = [(current, pool.submit(close_current, current)) for current in closing_processes]
            for current, result in pending:
                try:
                    result.result()
                except Exception as exc:
                    current.journal.emit('error', text='关闭原生执行失败：' + type(exc).__name__ + ': ' + str(exc))
                    errors.append(exc)
    if errors:
        raise errors[0]

