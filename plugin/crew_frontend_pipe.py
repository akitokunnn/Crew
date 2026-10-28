"""Bundled transport bridge for an independently selected supervisor source."""
import os
from pathlib import Path
import sys

from crew.frontend_pipe import run


if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1].startswith('-') or not Path(sys.argv[1]).is_file():
        print('Crew frontend bridge requires the selected supervisor entrypoint path', file=sys.stderr, flush=True)
        raise SystemExit(2)
    os._exit(run([sys.executable, *sys.argv[1:]]))
