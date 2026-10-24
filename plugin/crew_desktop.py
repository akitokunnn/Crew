"""Desktop-owned HTTP service: port 0 and a private stdin shutdown channel."""
import argparse
import errno
import os
from pathlib import Path
import sys
import threading

from crew import install, web


class HomeServiceLock:
    """OS-owned lock: all desktop profiles share one backend per Crew home."""

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
        # Closing the descriptor releases the kernel lock, including after a
        # crash. The file remains in place, avoiding stale lease/rename races.
        stream.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--isolated-sources", action="store_true")
    args = parser.parse_args(argv)
    home = Path(args.home).resolve()
    lock = HomeServiceLock(home)
    if not lock.acquire():
        print("CREW_BACKEND_IN_USE", file=sys.stderr, flush=True)
        return 75
    try:
        return serve(args, home)
    finally:
        lock.release()


def serve(args, home):
    plugin_root = Path(__file__).resolve().parent
    install.install(home, plugin_root)
    options = {"cwd": home, "migrate_manual_work": True}
    if args.isolated_sources:
        # Smoke tests must not inspect the user's Claude/Codex/VS Code sessions.
        options.update(agents_provider=lambda: [], claude_root=home / "isolated" / "claude",
                       codex_root=home / "isolated" / "codex", runs_file=home / "isolated" / "runs.jsonl",
                       vscode_state_db=home / "isolated" / "vscode.db")
    with web.create_server(home, plugin_root, args.port, **options) as server:
        def shutdown():
            # EOF means the owning desktop process is gone, too.
            for line in sys.stdin:
                if line.strip() == "shutdown":
                    break
            server.shutdown()
        threading.Thread(target=shutdown, daemon=True, name="crew-desktop-control").start()
        print(server.url, flush=True)
        server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
