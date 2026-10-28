"""Resolve native storage without inspecting or changing a user's configuration."""
import os
from pathlib import Path


def codex_home(env=None):
    environment = os.environ if env is None else env
    configured = environment.get('CODEX_HOME')
    return Path(configured).expanduser().resolve() if configured and configured.strip() else (Path.home() / '.codex').resolve()


def codex_sessions(env=None):
    return codex_home(env) / 'sessions'
