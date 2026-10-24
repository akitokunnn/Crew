"""Standalone desktop helper; only explicit update invokes an installer."""
import sys
from crew.tool_updates import main

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
