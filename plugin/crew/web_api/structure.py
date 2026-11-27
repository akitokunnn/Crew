"""Architecture board versions, rollback, restructure plans and leader merge / retire (docs/ASSISTANT_CAMPAIGN_DESIGN.md)."""
from contextlib import closing

from .. import board, domain_versions, domains, guard, restructure


def _keys(data, required, optional=()):
    if not isinstance(data, dict):
        raise board.BoardError('请求体必须是对象')
    extra = sorted(set(data) - set(required) - set(optional))
    missing = [key for key in required if key not in data]
    if extra or missing:
        raise board.BoardError('请求字段不对：' + '；'.join(
            (['多了 ' + '、'.join(extra)] if extra else []) + (['缺少 ' + '、'.join(missing)] if missing else [])))


def _number(query, key):
    values = query.get(key)
    if not values or not values[0].isdigit():
        raise board.BoardError('%s 必须是正整数' % key)
    return int(values[0])


def get_domain_versions(handler, service, url, query):
    with closing(guard.readonly(service.home)) as conn:
        project = service.selected_project(conn, query.get('project', [None])[0])
        handler._send(200, domain_versions.listing(conn, project, _number(query, 'before') if 'before' in query else None))


def get_domain_version(handler, service, url, query):
    with closing(guard.readonly(service.home)) as conn:
        project = service.selected_project(conn, query.get('project', [None])[0])
        handler._send(200, domains.rollback_preview(conn, project, _number(query, 'number')))


def post_domain_rollback(service, path, data, project_name, conn, now, project):
    _keys(data, ('project', 'number', 'board_version'))
    return domains.rollback(conn, project, data['number'], data['board_version'], now=now)


def post_restructure_preview(service, path, data, project_name):
    _keys(data, ('project', 'changes'))
    with closing(guard.readonly(service.home)) as conn:
        return restructure.preview(conn, service.selected_project(conn, data['project']), data['changes'], service.chat)


def post_restructure_apply(service, path, data, project_name, conn, now, project):
    _keys(data, ('project', 'changes', 'note', 'board_version', 'revision', 'handoffs'))
    return restructure.apply(service, conn, project, data, now)


def post_leader_handover(service, path, data, project_name, conn, now, project):
    """One-change plans: merge a leader into another, or retire one (with or without a successor)."""
    merge = path == '/api/leader/merge'
    other = 'into' if merge else 'successor'
    _keys(data, ('project', 'leader', other, 'handoff', 'board_version', 'revision'))
    handoff = data['handoff']
    if handoff is not None and not isinstance(handoff, dict):
        raise board.BoardError('handoff 必须是 {text, wake} 或 null')
    change = {'type': 'leader.merge' if merge else 'leader.retire', 'leader': data['leader'], other: data[other]}

    def name(session_id):
        return '「%s」' % (board.session(conn, session_id)['crew_name'] or session_id)
    note = ('合并 leader%s → %s' % (name(data['leader']), name(data['into'])) if merge else
            '退役 leader%s' % name(data['leader']) + ('，由%s接手' % name(data['successor']) if data['successor'] else ''))
    return restructure.apply(service, conn, project, {
        'changes': [change], 'note': note, 'board_version': data['board_version'], 'revision': data['revision'],
        'handoffs': [] if handoff is None else [{'index': 0, **handoff}]}, now)
