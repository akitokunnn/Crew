"""python -m crew."""
import sys

from .cli import main

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
raise SystemExit(main())
