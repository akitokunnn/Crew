"""HTTP handlers for the personal mind map. Routing lives in web.py (see docs/MINDMAP_DESIGN.md, 待接线)."""
from contextlib import closing

from . import board, mindmap, store


def _open(home):
    conn = store.connect(home)
    mindmap.ensure_schema(conn)
    return conn


def snapshot(home):
    """GET /api/personal/mindmap"""
    with closing(_open(home)) as conn:
        return mindmap.snapshot(conn)


def edit(home, data):
    """POST /api/personal/mindmap — body {ops, base_version}."""
    if not isinstance(data, dict) or set(data) != {'ops', 'base_version'}:
        raise board.BoardError('请求体必须正好包含 ops、base_version')
    with closing(_open(home)) as conn:
        return mindmap.apply(conn, data['ops'], data['base_version'])


def preview(home, data):
    """POST /api/personal/mindmap/preview — read-only conversion plan."""
    with closing(_open(home)) as conn:
        return mindmap.preview(conn, data)


def convert(home, data):
    """POST /api/personal/mindmap/convert — writes the target workspace's board; run it under the service write lock."""
    with closing(_open(home)) as conn:
        return mindmap.convert(conn, data)
