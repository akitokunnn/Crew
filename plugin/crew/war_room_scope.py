"""Approved file ownership and the once-per-room shared checkout audit."""
import json
import logging
import os
from pathlib import Path
import subprocess

from . import board, git_process, store, war_room


def contains(scope, path):
    scope, path = os.path.normcase(scope).replace('\\', '/'), os.path.normcase(path).replace('\\', '/')
    return (scope.rstrip('/') == path.rstrip('/') or
            (scope == './' and not os.path.isabs(path)) or
            (scope.endswith('/') and path.startswith(scope)))


def members(conn, room):
    version = room['approved_version'] or room['draft_version']
    plan = conn.execute('SELECT entries_json FROM war_room_plans WHERE room_id=? AND version=?',
                        (room['id'], version)).fetchone()
    result = {}
    if plan:
        historic = None
        for item in json.loads(plan[0]):
            if item['kind'] != 'assignment':
                continue
            sid = item['owner_session_id']
            separate = item.get('worktree')
            if separate is None and room['approved_version']:
                # Before per-owner selection, the checkout row was the sole record of isolation.
                if historic is None:
                    historic = {r['entry_id']: r for r in conn.execute(
                        'SELECT i.entry_id,i.owner_session_id,w.branch FROM war_room_items i LEFT JOIN war_room_worktrees w '
                        'ON w.room_id=i.room_id AND w.session_id=i.owner_session_id WHERE i.room_id=? AND i.plan_version=?',
                        (room['id'], version))}
                if item['entry_id'] in historic:
                    old = historic[item['entry_id']]
                    sid, separate = old['owner_session_id'], old['branch'] is not None
            key = sid or item['owner']
            if key not in result:
                result[key] = {'member': war_room._name(conn, sid) if sid else item['owner'], 'session_id': sid,
                               'mode': 'worktree' if separate else 'shared', 'paths': [], 'out_of_scope': []}
            member = result[key]
            for path in item.get('paths', ()):  # Older plans did not declare file ownership.
                if path not in member['paths']:
                    member['paths'].append(path)
    return result


def details(conn, room, reader):
    result = {'isolation_members': [], 'unowned_changes': []}
    if reader not in (board.HUMAN, room['lead_session_id']) and not war_room.is_member(conn, room['id'], reader):
        return result
    by_member = members(conn, room)
    from . import war_room_worktree
    if war_room_worktree.pre_upgrade(conn, room):
        for member in by_member.values():
            member['mode'] = 'shared'
    for detail, in conn.execute("SELECT detail FROM war_room_events WHERE room_id=? AND kind='out_of_scope' ORDER BY id",
                                (room['id'],)):
        event = json.loads(detail)
        sid = event['member']
        if sid is None:
            files = result['unowned_changes']
        else:
            # Include the audit of an owner whose last assignment was transferred.
            if sid not in by_member:
                by_member[sid] = {'member': war_room._name(conn, sid), 'session_id': sid,
                                  'mode': 'shared', 'paths': [], 'out_of_scope': []}
            files = by_member[sid]['out_of_scope']
        files.extend(path for path in event['files'] if path not in files)
    result['isolation_members'] = list(by_member.values())
    return result


def _read_git(root, *args):
    result = git_process.run(['-C', str(root), *args], timeout=10)
    if result.returncode:
        raise board.BoardError(result.stderr.strip() or '无法读取工作区 Git 状态')
    return result.stdout


def remember_base(conn, room_id, root, now):
    if not conn.execute("SELECT 1 FROM war_room_events WHERE room_id=? AND kind='isolation_base'", (room_id,)).fetchone():
        # Changes already in the folder at approval (an earlier room's, the user's own) belong to nobody here.
        dirty = _read_git(root, 'diff', '--name-only', '--no-renames', '--relative', '-z', 'HEAD', '--', '.')
        dirty += _read_git(root, 'ls-files', '--others', '--exclude-standard', '-z', '--', '.')
        war_room._event(conn, room_id, board.HUMAN, 'isolation_base',
                        {'commit': _read_git(root, 'rev-parse', '--verify', 'HEAD^{commit}').strip(),
                         'dirty': sorted({path for path in dirty.split('\0') if path})}, now)


def queue(conn, room_id, sid, now):
    if not conn.execute("SELECT 1 FROM war_room_events WHERE room_id=? AND actor=? AND kind='scope_pending'",
                                  (room_id, sid)).fetchone():
        war_room._event(conn, room_id, sid, 'scope_pending', {}, now)


def _tool_paths(events, plans):
    from . import codex_guard
    failed = {e['tool_use_id'] for e in events if e['type'] == 'tool_result' and e.get('is_error')}
    turn = None
    for event in events:
        if event['type'] == 'input_delivery' and event['status'] == 'submitted':
            turn = store.parse_iso(event['at'])
        elif event['type'] == 'turn_result':
            turn = None
        if event['type'] != 'assistant':
            continue
        at = turn or store.parse_iso(event['at'])
        scopes = next((paths for approved, paths in plans if approved <= at), ())
        if not scopes:
            continue
        for block in event['content']:
            if block['type'] != 'tool_use' or block['id'] in failed:
                continue
            name, data = block['name'], block['input']
            if name in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit'):
                path = data.get('file_path') or data.get('notebook_path')
                if path:
                    yield path, scopes
            elif name == 'ApplyPatch':
                for change in data['changes']:
                    yield change['path'], scopes
                    kind = change.get('kind')
                    if isinstance(kind, dict) and kind.get('move_path'):
                        yield kind['move_path'], scopes
            elif name in ('apply_patch', 'functions.apply_patch'):
                try:
                    for path in codex_guard.patch_paths(data.get('command') or data.get('arguments')):
                        yield path, scopes
                except codex_guard.InvalidHook as exc:
                    logging.getLogger(__name__).warning('无法解析 apply_patch 工具记录 %s：%s', block['id'], exc)


def _outside_tools(manager, conn, room, sid, root):
    from .chat_journal import Journal
    executions = conn.execute('SELECT DISTINCT e.id FROM goal_executions e JOIN war_room_items i ON i.goal_id=e.goal_id '
                              'WHERE i.room_id=? AND e.session_id=?', (room, sid)).fetchall()
    outside = set()
    cwd = Path(board.session(conn, sid)['cwd'])
    # A revised assignment cannot make a previous, authorized turn look like a violation.
    plans = []
    for stamp, entries in conn.execute("SELECT json_extract(approval,'$.at'),entries_json FROM war_room_plans "
                                       "WHERE room_id=? AND approval IS NOT NULL ORDER BY version DESC", (room,)):
        paths = [p for entry in json.loads(entries) if entry['owner_session_id'] == sid and not entry.get('worktree')
                 for p in entry.get('paths', ())]
        plans.append((store.parse_iso(stamp), paths))
    for key, in executions:
        events = Journal(manager.path(key), None).events
        for path, scopes in _tool_paths(events, plans):
            absolute = (cwd / path).resolve()
            if absolute.is_relative_to(root):
                path = absolute.relative_to(root).as_posix()
                if any(contains(scope, path) for scope in scopes):
                    continue
            else:
                path = absolute.as_posix()
            outside.add(path)
    return sorted(outside)


def _record(conn, room, sid, source, outside, now):
    from . import war_room_plan
    actor = sid or board.HUMAN
    with board.transaction(conn):
        if conn.execute("SELECT 1 FROM war_room_events WHERE room_id=? AND actor=? AND kind='scope_checked'",
                        (room, actor)).fetchone():
            return
        war_room._event(conn, room, actor, 'scope_checked', {'source': source}, now)
        if outside:
            war_room._event(conn, room, actor, 'out_of_scope', {'member': sid, 'files': outside[:50], 'source': source}, now)
            current = war_room._room(conn, room)
            if current['status'] == 'open':
                title = war_room._name(conn, sid)+' 改了分工范围外的文件：' if sid else '共用目录里有不属于任何分工的改动：'
                war_room_plan._notify(conn, current, current['lead_session_id'], title+'、'.join(outside[:5])+'……请核对。', now)
            war_room._touch(conn, room, current['project'])


def sweep(manager, conn, now):
    from . import war_room_worktree
    pending = conn.execute(
        "SELECT e.room_id,e.actor,e.kind,e.detail FROM war_room_events e JOIN war_rooms r ON r.id=e.room_id "
        "LEFT JOIN war_room_events c ON c.room_id=e.room_id AND c.actor=e.actor AND c.kind='scope_checked' "
        "WHERE (e.kind='scope_pending' OR (e.kind='isolation_base' AND r.status='closed')) "
        "AND c.id IS NULL ORDER BY e.kind='isolation_base', e.id").fetchall()
    for room_id, sid, kind, detail in pending:
        root = Path(war_room_worktree.repository_record(conn, room_id)['root']).resolve()
        try:
            if kind == 'scope_pending':
                if war_room_worktree._occupied(manager, conn, {'room_id': room_id, 'session_id': sid}):
                    continue
                outside = _outside_tools(manager, conn, room_id, sid, root)
                _record(conn, room_id, sid, 'tools', outside, now)
            else:
                if conn.execute("SELECT 1 FROM war_room_events p WHERE p.room_id=? AND p.kind='scope_pending' AND NOT EXISTS "
                                "(SELECT 1 FROM war_room_events c WHERE c.room_id=p.room_id AND c.actor=p.actor AND c.kind='scope_checked')",
                                (room_id,)).fetchone():
                    continue
                scopes = [path for m in members(conn, war_room._room(conn, room_id)).values() for path in m['paths']]
                base = json.loads(detail)
                names = _read_git(root, 'diff', '--name-only', '--no-renames', '--relative', '-z', base['commit'], '--', '.')
                names += _read_git(root, 'ls-files', '--others', '--exclude-standard', '-z', '--', '.')
                before = set(base['dirty'])
                outside = sorted({path for path in names.split('\0') if path and path not in before
                                  and not any(contains(s, path) for s in scopes)})
                _record(conn, room_id, None, 'git', outside, now)
        except (OSError, board.BoardError, subprocess.TimeoutExpired):
            logging.getLogger(__name__).exception('作战室 #%d 文件范围检查失败，下轮重试', room_id)
