"""Desktop-owned HTTP service: port 0 and a private stdin shutdown channel."""
import argparse
from pathlib import Path
import sys
import threading

from crew import install, web
from crew.service_lock import HomeServiceLock  # Re-export for existing owners.


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
    # Discovery belongs to opening a workspace. Startup must neither choose an
    # old data source silently nor prevent all workspaces opening on a conflict.
    options = {"cwd": home, "isolated_sources": args.isolated_sources}
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
        # The owner shutdown channel should not wait for the default half-second
        # HTTP polling tick; this changes no in-flight operation deadline.
        server.serve_forever(poll_interval=.1)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
