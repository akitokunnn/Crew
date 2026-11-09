"""Resolve the content a user is reading before delivering their message."""
from . import behavior_prompts, board, documents, goals


def context(conn, project, reading, resolve_file, session_id):
    if not isinstance(reading, dict) or reading.get('kind') not in ('report', 'document', 'file'):
        raise board.BoardError('阅读内容格式不正确')
    kind = reading['kind']
    required = {'report': ('goal_id', 'report_id'), 'document': ('document_id',), 'file': ('path',)}
    for field in required[kind]:
        if field not in reading:
            raise board.BoardError('阅读内容缺少 ' + field)

    if kind == 'report':
        goal = goals.get(conn, reading['goal_id'], project)
        report = goals.get_report(conn, reading['report_id'])
        if report['goal_id'] != goal['id']:
            raise board.BoardError('报告不属于此目标')
        lines = [f"任务：「{goal['title']}」（目标标识 {goal['id']}）",
                 f"交付报告：第 {report['round']} 轮 · 第 {report['version']} 稿（报告标识 {report['id']}）"]
        if not report['current']:
            lines.append('这不是本轮最新一稿。')
    elif kind == 'document':
        info = resolve_file(None, None, project, None, reading['document_id'])
        if info['error'] is not None:
            raise board.BoardError(info['error'])
        document = documents.get(conn, project, reading['document_id'])
        lines = [f"文档：「{document['title']}」", f"路径：{info['path']}"]
    else:
        info = resolve_file(reading.get('session_id') or None, reading['path'], project, None, None)
        if info['error'] is not None:
            raise board.BoardError(info['error'])
        lines = [f"文件：{info['path']}"]

    return behavior_prompts.text(conn, 'reading_context', project=project, session_id=session_id,
                                 values={'reading': '\n'.join(lines)})
