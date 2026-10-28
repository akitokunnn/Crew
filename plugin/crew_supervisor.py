"""Entrypoint for the HOME-wide Crew system administrator."""
import sys


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    frontend = '--frontend-pipe' in args
    if frontend:
        args = [value for value in args if value != '--frontend-pipe']
    if frontend and not any(value in ('--probe', '--help', '-h') for value in args):
        import os
        from pathlib import Path
        from crew.frontend_pipe import run
        # Every relay is a daemon. Exit only this short-lived bridge, avoiding
        # interpreter teardown waiting on a still-running owner's pipe readers.
        os._exit(run([sys.executable, str(Path(__file__).resolve()), *args]))
    from crew.supervisor import main
    raise SystemExit(main(args))
