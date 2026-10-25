"""Desktop helper for updating to the Crew version bundled with this desktop."""
import sys
from crew.updater import main

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
