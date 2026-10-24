"""Kernel-owned exclusion shared by every production Crew backend entry point."""
import errno
import os
from pathlib import Path


class HomeServiceLock:
    """Serialize backend initialization and lifetime for one Crew HOME."""

    def __init__(self, home):
        self.path = Path(home) / "desktop" / "service.lock"
        self.stream = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if self.path.stat().st_size == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return False
            raise
        self.stream = stream
        return True

    def release(self):
        if self.stream is None:
            return
        stream, self.stream = self.stream, None
        # Kernel close releases ownership after graceful exit and crashes.
        # Keep the same file in place to avoid stale lease and rename races.
        stream.close()
