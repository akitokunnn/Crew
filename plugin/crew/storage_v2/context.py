"""Budgeted, source-versioned context and explicit portable handoffs; no AI calls."""
from contextlib import closing
import json
from pathlib import Path
import re
import shutil

from .database import StorageError, now_iso
from .objects import canonical_uuid


MAX_CONTEXT_CHARS = 6000
MAX_HANDOFF_FILE_BYTES = 25 * 1024 * 1024
TERMINAL = ('completed', 'failed', 'cancelled')


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def _visible(alias, table, *, mutable=False):
    return ((' AND ' + alias + '.deleted_at IS NULL') if mutable else '') + (
        " AND NOT EXISTS(SELECT 1 FROM entity_tombstones t WHERE t.workspace_id=" + alias + ".workspace_id "
        "AND t.entity_type='" + table + "' AND t.entity_id=" + alias + ".id AND t.state='active' AND t.deleted_at IS NULL)")


def _scope(conn, workspace_id, goal_id):
    canonical_uuid(workspace_id)
    workspace = conn.execute('SELECT * FROM workspaces w WHERE w.id=? AND w.deleted_at IS NULL', (workspace_id,)).fetchone()
    deleted = conn.execute("SELECT 1 FROM entity_tombstones WHERE workspace_id=? AND entity_type='workspaces' "
                           "AND entity_id=? AND state='active' AND deleted_at IS NULL", (workspace_id, workspace_id)).fetchone()
    if workspace is None or deleted is not None:
        raise StorageError('Workspace does not exist: ' + workspace_id)
    if goal_id is not None:
        canonical_uuid(goal_id)
        if conn.execute('SELECT 1 FROM goals g WHERE g.workspace_id=? AND g.id=?' +
                        _visible('g', 'goals', mutable=True), (workspace_id, goal_id)).fetchone() is None:
            raise StorageError('Goal does not belong to this workspace: ' + goal_id)
    return dict(workspace)


def _tokens(query):
    if query is None:
        return []
    if not isinstance(query, str) or len(query) > 1000:
        raise StorageError('Context query must be text of at most 1000 characters')
    return list(dict.fromkeys(re.findall(r'[\w\u3400-\u9fff]{2,}', query.casefold())))[:12]


def _candidates(conn, workspace_id, goal_id, tokens, agent_id=None):
    sources = []

    def add(table, row, title, text, priority, *, representation='record', required=False, fields=None):
        folded = (title + '\n' + text).casefold()
        score = sum(token in folded for token in tokens)
        if tokens and not score and not required:
            return
        sources.append(dict(source_id=table + '/' + row['id'] + '/' + representation,
                            table=table, id=row['id'], version=row['revision'], title=title,
                            text=text, source_fields=fields or [], priority=priority + 10 * score))

    goal_where = 'g.id=?' if goal_id is not None else "g.status NOT IN ('completed','failed','cancelled')"
    parameters = (workspace_id, goal_id) if goal_id is not None else (workspace_id,)
    if goal_id is None and agent_id is not None:
        goal_where += ' AND (g.owner_agent_id=? OR EXISTS(SELECT 1 FROM goal_assignments a JOIN goal_rounds r ON r.workspace_id=a.workspace_id AND r.id=a.goal_round_id WHERE a.workspace_id=g.workspace_id AND r.goal_id=g.id AND a.agent_id=? AND a.deleted_at IS NULL))'
        parameters += (agent_id,agent_id)
    goals = conn.execute('SELECT g.* FROM goals g WHERE g.workspace_id=? AND ' + goal_where +
                         _visible('g', 'goals', mutable=True) +
                         ' ORDER BY g.priority DESC,g.updated_at DESC,g.id LIMIT 8', parameters).fetchall()
    for goal in goals:
        add('goals', goal, goal['title'],
            '目标：' + goal['title'] + '\n状态：' + goal['status'] + '\n原因：' + (goal['reason'] or '未记录') +
            '\n主责 agent：' + goal['owner_agent_id'], 150, required=goal_id is not None,
            fields=['title', 'status', 'reason', 'owner_agent_id'])
        if goal['current_requirement_id']:
            requirement = conn.execute('SELECT r.* FROM goal_requirements r WHERE r.workspace_id=? AND r.id=?' +
                                       _visible('r', 'goal_requirements'),
                                       (workspace_id, goal['current_requirement_id'])).fetchone()
            if requirement is not None:
                text = ('原始要求：\n' + requirement['request_markdown'] + '\n验收标准（原记录 JSON）：\n' +
                        requirement['acceptance_json'] + '\n已确认约束（原记录 JSON）：\n' + requirement['constraints_json'])
                add('goal_requirements', requirement, '需求 v%s · %s' % (requirement['version_no'], goal['title']),
                    text, 200, required=goal_id is not None,
                    fields=['request_markdown', 'acceptance_json', 'constraints_json'])

    owner=goals[0]['owner_agent_id'] if goal_id and goals else agent_id
    context_scope="i.scope='workspace'"
    context_args=[workspace_id]
    if goal_id:context_scope+=" OR (i.scope='goal' AND i.goal_id=?)";context_args.append(goal_id)
    if owner:context_scope+=" OR (i.scope='agent' AND i.owner_agent_id=?)";context_args.append(owner)
    for row in conn.execute('SELECT v.*,i.title AS item_title,i.protected AS item_protected FROM context_items i '
                            'JOIN context_versions v ON v.workspace_id=i.workspace_id AND v.id=i.current_version_id '
                            "WHERE i.workspace_id=? AND i.state='active' AND " +
                            '('+context_scope+')' +
                            _visible('i', 'context_items', mutable=True) + _visible('v', 'context_versions') +
                            ' ORDER BY i.protected DESC,i.updated_at DESC,i.id LIMIT 24',
                            tuple(context_args)):
        if row['text_content'] is not None:
            add('context_versions', row, row['item_title'], row['text_content'],
                180 if row['item_protected'] else 110, fields=['text_content'])

    for row in conn.execute('SELECT d.*,v.title AS question_title,v.question_markdown,v.why_user,v.impact_markdown '
                            'FROM decisions d JOIN decision_versions v ON v.workspace_id=d.workspace_id AND v.id=d.current_version_id '
                            "WHERE d.workspace_id=? AND d.status='open'" +
                            (' AND EXISTS(SELECT 1 FROM goal_rounds r WHERE r.workspace_id=d.workspace_id '
                             'AND r.id=d.goal_round_id AND r.goal_id=?)' if goal_id else '') +
                            _visible('d', 'decisions', mutable=True) + _visible('v', 'decision_versions') +
                            ' ORDER BY d.blocking DESC,d.updated_at DESC,d.id LIMIT 12',
                            (workspace_id, goal_id) if goal_id else (workspace_id,)):
        text = ('待拍板：' + row['question_markdown'] + '\n需要用户的原因：' + row['why_user'] +
                '\n影响：' + row['impact_markdown'] + '\n挂起：' + ('是' if row['held_at'] else '否'))
        add('decisions', row, row['question_title'], text, 170, required=goal_id is not None,
            fields=['current_version_id', 'status', 'held_at', 'question_markdown', 'why_user', 'impact_markdown'])

    for row in conn.execute("SELECT t.* FROM todos t WHERE t.workspace_id=? AND t.status='open'" +
                            (' AND EXISTS(SELECT 1 FROM goal_rounds r WHERE r.workspace_id=t.workspace_id '
                             'AND r.id=t.goal_round_id AND r.goal_id=?)' if goal_id else '') +
                            (' AND t.requester_agent_id=?' if not goal_id and agent_id else '') +
                            _visible('t', 'todos', mutable=True) +
                            ' ORDER BY t.blocking DESC,t.updated_at DESC,t.id LIMIT 16',
                            (workspace_id, goal_id) if goal_id else (workspace_id,agent_id) if agent_id else (workspace_id,)):
        add('todos', row, row['title'],
            '尚未完成的人工事项：' + row['title'] + '\n步骤：' + row['steps_markdown'] +
            '\n验收：' + row['verification_markdown'], 160, required=goal_id is not None,
            fields=['title', 'steps_markdown', 'verification_markdown', 'blocking', 'status'])

    if goal_id:
        for row in conn.execute('SELECT a.* FROM goal_assignments a JOIN goal_rounds r '
                                'ON r.workspace_id=a.workspace_id AND r.id=a.goal_round_id '
                                "WHERE a.workspace_id=? AND r.goal_id=? AND a.status='assigned'" +
                                _visible('a', 'goal_assignments', mutable=True) +
                                ' ORDER BY a.updated_at DESC,a.id LIMIT 12', (workspace_id, goal_id)):
            add('goal_assignments', row, '指派 · ' + row['agent_id'],
                '职责：' + row['scope_markdown'] + '\n完成条件：' + row['done_when_markdown'], 130,
                fields=['agent_id', 'scope_markdown', 'done_when_markdown', 'status'])

    for row in conn.execute('SELECT p.* FROM reports p WHERE p.workspace_id=?' +
                            (' AND EXISTS(SELECT 1 FROM goal_rounds r WHERE r.workspace_id=p.workspace_id '
                             'AND r.id=p.goal_round_id AND r.goal_id=?)' if goal_id else '') +
                            (' AND p.author_agent_id=?' if not goal_id and agent_id else '') +
                            _visible('p', 'reports') + ' ORDER BY p.created_at DESC,p.id LIMIT 12',
                            (workspace_id, goal_id) if goal_id else (workspace_id,agent_id) if agent_id else (workspace_id,)):
        representation = 'body' if tokens else 'summary'
        content = row['body_markdown'] if tokens else row['summary']
        add('reports', row, row['title'], '报告结论：' + row['outcome'] + '\n' + content +
            ('\nHTML artifact version：' + row['html_artifact_version_id'] if row['html_artifact_version_id'] else ''),
            100, representation=representation, fields=['outcome', 'body_markdown' if tokens else 'summary', 'html_artifact_version_id'])
    message_args=[workspace_id]
    message_scope=''
    if goal_id:message_scope+=' AND c.goal_id=?';message_args.append(goal_id)
    elif agent_id:message_scope+=' AND c.owner_agent_id=?';message_args.append(agent_id)
    if tokens:
        message_scope+=' AND ('+' OR '.join('lower(p.text_content) LIKE ?' for _ in tokens)+')'
        message_args.extend('%'+t+'%' for t in tokens)
    for row in conn.execute("SELECT p.*,m.author_name_snapshot FROM message_parts p JOIN messages m ON m.workspace_id=p.workspace_id AND m.id=p.message_id JOIN conversations c ON c.workspace_id=m.workspace_id AND c.id=m.conversation_id LEFT JOIN message_positions x ON x.workspace_id=m.workspace_id AND x.message_id=m.id WHERE p.workspace_id=? AND p.kind IN ('text','markdown','notice')"+message_scope+_visible('p','message_parts')+_visible('m','messages')+_visible('c','conversations',mutable=True)+" AND NOT EXISTS(SELECT 1 FROM messages next WHERE next.workspace_id=m.workspace_id AND next.supersedes_message_id=m.id) ORDER BY COALESCE(x.accepted_sequence,0) DESC,m.id DESC,p.part_index DESC LIMIT 20",tuple(message_args)):
        add('message_parts',row,'历史消息 · '+(row['author_name_snapshot'] or '未记名'),row['text_content'],75,fields=['message_id','text_content'])
    return sorted(sources, key=lambda row: (-row['priority'], row['source_id']))


def _payload_size(snippets):
    return len(_json(snippets)) if snippets else 0


def _retrieve(conn, workspace_id, *, query=None, goal_id=None, agent_id=None, known_versions=None, limit_chars=MAX_CONTEXT_CHARS):
    if type(limit_chars) is not int or not 0 <= limit_chars <= MAX_CONTEXT_CHARS:
        raise StorageError('Context budget must be an integer between 0 and 6000 characters')
    if known_versions is not None and not isinstance(known_versions, dict):
        raise StorageError('known_versions must be a source ID/version mapping')
    _scope(conn, workspace_id, goal_id)
    if agent_id is not None:
        canonical_uuid(agent_id)
        if conn.execute('SELECT 1 FROM agents WHERE workspace_id=? AND id=? AND deleted_at IS NULL',(workspace_id,agent_id)).fetchone() is None:
            raise StorageError('Context agent does not belong to this workspace')
    candidates = _candidates(conn, workspace_id, goal_id, _tokens(query),agent_id=agent_id)
    known, snippets, versions, omitted, unchanged = known_versions or {}, [], {}, [], 0
    for source in candidates:
        if str(known.get(source['source_id'])) == str(source['version']):
            unchanged += 1
            continue
        snippet = {key: value for key, value in source.items() if key != 'priority'}
        snippet['truncated'] = False
        if _payload_size(snippets + [snippet]) > limit_chars:
            original = snippet['text']
            low, high = 0, len(original)
            while low < high:
                mid = (low + high + 1) // 2
                snippet.update(text=original[:mid] + '\n[内容已截断；按来源继续读取]', truncated=True)
                if _payload_size(snippets + [snippet]) <= limit_chars:
                    low = mid
                else:
                    high = mid - 1
            snippet.update(text=original[:low] + '\n[内容已截断；按来源继续读取]', truncated=True)
            if low == 0 or _payload_size(snippets + [snippet]) > limit_chars:
                omitted.append({'source_id': source['source_id'], 'reason': 'character_budget'})
                continue
        snippets.append(snippet)
        if not snippet['truncated']:
            versions[snippet['source_id']] = snippet['version']
    return dict(workspace_id=workspace_id, goal_id=goal_id, snippets=snippets, versions=versions,
                used_chars=_payload_size(snippets), limit_chars=limit_chars,
                omitted=omitted[:30], omitted_count=len(omitted), unchanged_count=unchanged,
                budget_basis='canonical JSON of snippets; administration fields are outside the prompt budget')


def retrieve(repo, workspace_id, *, query=None, goal_id=None, agent_id=None, known_versions=None, limit_chars=MAX_CONTEXT_CHARS):
    """Return bounded incremental snippets from one consistent read snapshot."""
    with closing(repo.connect()) as conn:
        conn.execute('BEGIN')
        try:
            return _retrieve(conn, workspace_id, query=query, goal_id=goal_id,
                             agent_id=agent_id,known_versions=known_versions, limit_chars=limit_chars)
        finally:
            conn.rollback()


def export_handoff(repo, workspace_id, destination, *, goal_id=None, max_chars=MAX_CONTEXT_CHARS):
    """Write a selected-context handoff, not a complete workspace/native backup."""
    destination = Path(destination).expanduser().resolve()
    if not destination.parent.is_dir():
        raise StorageError('Handoff parent directory does not exist: ' + str(destination.parent))
    created = []
    made_directory = False
    with closing(repo.connect()) as conn:
        conn.execute('BEGIN')
        try:
            workspace = _scope(conn, workspace_id, goal_id)
            context = _retrieve(conn, workspace_id, goal_id=goal_id, limit_chars=max_chars)
            goal_query = ' AND g.id=?' if goal_id else " AND g.status NOT IN ('completed','failed','cancelled')"
            goals = [dict(row) for row in conn.execute('SELECT g.id,g.title,g.status,g.current_requirement_id '
                     'FROM goals g WHERE g.workspace_id=?' + goal_query + _visible('g', 'goals', mutable=True) +
                     ' ORDER BY g.priority DESC,g.id LIMIT 20', (workspace_id, goal_id) if goal_id else (workspace_id,))]
            requirements = []
            for goal in goals:
                if goal['current_requirement_id']:
                    requirement = conn.execute('SELECT r.id,r.goal_id,r.version_no,r.request_markdown,r.acceptance_json,r.constraints_json '
                                              'FROM goal_requirements r WHERE r.workspace_id=? AND r.id=?' +
                                              _visible('r', 'goal_requirements'),
                                              (workspace_id, goal['current_requirement_id'])).fetchone()
                    if requirement is not None:
                        requirements.append(dict(requirement))
            reports = [dict(row) for row in conn.execute('SELECT p.id,p.title,p.outcome,p.summary,p.html_artifact_version_id '
                'FROM reports p WHERE p.workspace_id=?' +
                (' AND EXISTS(SELECT 1 FROM goal_rounds r WHERE r.workspace_id=p.workspace_id AND r.id=p.goal_round_id '
                 'AND r.goal_id=?)' if goal_id else '') + _visible('p', 'reports') +
                ' ORDER BY p.created_at DESC,p.id LIMIT 12', (workspace_id, goal_id) if goal_id else (workspace_id,))]
            payload = dict(format='crew-handoff', format_version=1, created_at=now_iso(),
                workspace={'id': workspace_id, 'name': workspace['name']}, goal_id=goal_id,
                native_resume_supported=False, complete_workspace_backup=False,
                authority='User-readable Crew records only; no permission to start execution is transferred.',
                context=context, goals=goals, requirements=requirements, reports=reports,
                files=[], unavailable_files=[],
                limits={'context_chars': max_chars, 'requirements_are_source_records_not_prompt_budget': True,
                        'attachment_bytes': MAX_HANDOFF_FILE_BYTES})
            # Source requirements are explicit original records, not an invented
            # summary. Keep a hard independent cap on this optional handoff data.
            if len(_json(requirements).encode('utf-8')) > MAX_HANDOFF_FILE_BYTES:
                raise StorageError('Selected original requirements exceed the handoff file budget')
            destination.mkdir()
            made_directory = True
            total_bytes = 0
            included_versions = {}
            for report in reports:
                version_id = report['html_artifact_version_id']
                if not version_id:
                    continue
                if version_id in included_versions:
                    report['file'] = included_versions[version_id]
                    continue
                artifact = conn.execute('SELECT v.id,b.sha256,b.size_bytes,b.state FROM artifact_versions v '
                    'JOIN blob_objects b ON b.workspace_id=v.workspace_id AND b.id=v.blob_id '
                    'WHERE v.workspace_id=? AND v.id=?' + _visible('v', 'artifact_versions') +
                    _visible('b', 'blob_objects', mutable=True), (workspace_id, version_id)).fetchone()
                if artifact is None or artifact['state'] != 'verified':
                    payload['unavailable_files'].append({'artifact_version_id': version_id, 'reason': 'missing_or_unverified_metadata'})
                    continue
                if total_bytes + artifact['size_bytes'] > MAX_HANDOFF_FILE_BYTES:
                    payload['unavailable_files'].append({'artifact_version_id': version_id, 'reason': 'attachment_budget'})
                    continue
                relative = 'report-' + artifact['id'] + '.html'
                target = destination / relative
                source_opened = False
                try:
                    with repo.objects.open(workspace_id, artifact['sha256'], expected_size=artifact['size_bytes']) as source:
                        source_opened = True
                        with target.open('xb') as output:
                            created.append(target)
                            shutil.copyfileobj(source, output, length=1024 * 1024)
                except FileNotFoundError:
                    if source_opened:
                        raise
                    payload['unavailable_files'].append({'artifact_version_id': version_id, 'reason': 'content_not_on_this_device'})
                    continue
                total_bytes += artifact['size_bytes']
                report['file'] = relative
                included_versions[version_id] = relative
                payload['files'].append({'path': relative, 'sha256': artifact['sha256'], 'size_bytes': artifact['size_bytes'],
                                         'artifact_version_id': version_id})
            lines = ['# Crew 交接 · ' + workspace['name'], '',
                     '此包是有来源的选定工作记录；不保证恢复原生 Codex/Claude 会话，不授予执行权限。', '',
                     '原始要求、验收与已确认约束见 handoff.json 的 requirements；其中保留的是原记录。', '',
                     '## 当前目标', '']
            lines.extend('- ' + goal['title'] + ' · ' + goal['status'] + ' · ' + goal['id'] for goal in goals)
            lines.extend(['', '## 本轮有限上下文', ''])
            for snippet in context['snippets']:
                lines.extend(['### ' + snippet['title'], '', snippet['text'], '',
                              '来源：' + snippet['source_id'] + ' · revision ' + str(snippet['version']), ''])
            lines.extend(['## 已记录报告', ''])
            for report in reports:
                link = '[' + report['title'].replace(']', '') + '](' + report['file'] + ')' if report.get('file') else report['title']
                lines.append('- ' + link + ' · ' + report['outcome'] + ' · report ' + report['id'])
            if context['omitted_count'] or payload['unavailable_files']:
                lines.extend(['', '有材料因预算或本机文件未下载而未包含；具体来源和原因见 handoff.json。'])
            markdown = destination / 'handoff.md'
            with markdown.open('x', encoding='utf-8', newline='\n') as output:
                created.append(markdown)
                output.write('\n'.join(lines) + '\n')
            manifest = destination / 'handoff.json'
            with manifest.open('x', encoding='utf-8', newline='\n') as output:
                created.append(manifest)
                json.dump(payload, output, ensure_ascii=False, indent=2)
                output.write('\n')
            return {'directory': str(destination), 'manifest': str(manifest), 'markdown': str(markdown),
                    'files': len(payload['files']), 'unavailable_files': payload['unavailable_files'],
                    'used_chars': context['used_chars'], 'complete_workspace_backup': False,
                    'native_resume_supported': False}
        except BaseException:
            for path in reversed(created):
                path.unlink(missing_ok=True)
            if made_directory:
                destination.rmdir()
            raise
        finally:
            conn.rollback()
