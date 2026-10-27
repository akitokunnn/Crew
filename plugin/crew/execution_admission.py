"""Admission for model work, independent of transports and execution routing.

An admitted outer operation owns one in-flight slot. Nested operations on the
same thread share it, so an update can close the door without deadlocking work
which it has already accepted. Background delivery observes a closed door and
leaves durable queues untouched.
"""
from contextlib import contextmanager
import threading

from . import board


PAUSED_FOR_UPDATE = "Crew 正在准备更新，暂不接受新任务；更新完成后再试"
MAINTENANCE_HELD = "正在压缩该人员的记忆，完成后再发送"


class UpdatePaused(board.Conflict):
    """New model work is refused while a desktop update holds the gate."""


class WorkAdmission:
    def __init__(self):
        self._condition = threading.Condition()
        self._gate = None
        self._in_flight = 0
        self._local = threading.local()

    @contextmanager
    def work(self, background=False):
        depth = getattr(self._local, "depth", 0)
        if depth == 0:
            with self._condition:
                paused = self._gate is not None
                if not paused:
                    self._in_flight += 1
            if paused:
                if not background:
                    raise UpdatePaused(PAUSED_FOR_UPDATE)
                yield False
                return
        self._local.depth = depth + 1
        try:
            yield True
        finally:
            self._local.depth = depth
            if depth == 0:
                with self._condition:
                    self._in_flight -= 1
                    self._condition.notify_all()

    def paused(self):
        with self._condition:
            return self._gate

    def close(self, gate, timeout):
        """Hold new work and wait for accepted outer operations to drain.

        A failed wait releases this attempted hold. A different holder may not
        replace an existing hold or reopen it with a stale identity.
        """
        with self._condition:
            if self._gate is not None:
                raise board.Conflict("已有 Crew 更新正在准备")
            self._gate = gate
            if self._condition.wait_for(lambda: self._in_flight == 0, timeout):
                return True
            self._gate = None
            return False

    def open(self, gate):
        with self._condition:
            if self._gate != gate:
                return False
            self._gate = None
            return True
