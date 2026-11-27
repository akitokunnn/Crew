"""Workspace mind map operations used by the HTTP and AI registry."""
from contextlib import closing

from . import board, mindmap, store


def snapshot(home, project):
    """GET /api/personal/mindmap"""
    with closing(store.connect(home)) as conn:
        conn.execute('BEGIN')
        return mindmap.snapshot(conn, project)


def edit(home, data):
    """POST /api/personal/mindmap — body {project, ops, base_version}."""
    if not isinstance(data, dict) or set(data) != {'project', 'ops', 'base_version'}:
        raise board.BoardError('请求体必须正好包含 project、ops、base_version')
    with closing(store.connect(home)) as conn:
        return mindmap.apply(conn, data['ops'], data['base_version'], project=data['project'])


def preview(home, data):
    """POST /api/personal/mindmap/preview — read-only conversion plan."""
    with closing(store.connect(home)) as conn:
        conn.execute('BEGIN')
        return mindmap.preview(conn, data)


def convert(home, data):
    """POST /api/personal/mindmap/convert — writes the target workspace's board; run it under the service write lock."""
    with closing(store.connect(home)) as conn:
        return mindmap.convert(conn, data)
