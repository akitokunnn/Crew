"""Locate decoupled frontend assets without coupling frontend edits to plugin/crew/."""
import os
from pathlib import Path


def resolve(plugin_root=None):
    override = os.environ.get("CREW_WEB_ROOT")
    if override:
        return Path(override).resolve()
    base = Path(__file__).resolve().parents[1] if plugin_root is None else Path(plugin_root).resolve()
    sibling = base.parent / "frontend"
    if sibling.is_dir():
        return sibling
    return base / "crew" / "web"
