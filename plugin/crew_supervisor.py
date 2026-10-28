"""Entrypoint for the HOME-wide Crew system administrator."""
import sys

from crew.supervisor import main


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
