"""Project deliverables: original files, explicit metadata, and bounded history indexing."""
import contextlib
import json
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import board, goals, store, workspace_settings

KINDS = {'summary', 'report', 'guide', 'other'}
EXTENSIONS = {'.md', '.markdown', '.txt', '.rst', '.adoc', '.html', '.htm', '.pdf', '.doc', '.docx',
              '.rtf', '.xls', '.xlsx', '.csv', '.ppt', '.pptx', '.odt', '.ods', '.odp', '.json',
              '.png', '.jpg', '.jpeg', '.webp', '.svg'}
AUTO_EXTENSIONS = EXTENSIONS - {'.json', '.png', '.jpg', '.jpeg', '.webp', '.svg'}
MAX_BYTES = 2 * 1024 * 1024
MAX_LINES = 2000
MAX_SESSIONS = 20
MAX_LINE = 256 * 1024
LINK = re.compile(r'\[([^\]\n]{1,300})\]\(\s*(?:<([^>\n]+)>|((?:\\.|[^()\n]|\([^()\n]*\))+?))\s*\)')


@contextlib.contextmanager
def _atomic(conn):
    if conn.in_transaction:
        yield
    else:
        with board.transaction(conn):
            yield


def _clean(value, name, limit, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise board.BoardError('%s 必须是非空文本（最多 %s 字符）' % (name, limit))
    return value.strip()


def _owner(conn, session_id, project=None):
    owner = board.session(conn, _clean(session_id, 'session_id', 200))
    if not owner['project'] or project is not None and owner['project'] != project:
        raise board.BoardError('文档提交者不属于当前工作区')
    project_info = board.project(conn, owner['project'])
    return owner, project_info


def _path_text(value):
    value = _clean(value, 'path', 4096).strip('<>')
    if '\x00' in value:
        raise board.BoardError('文件路径无效')
    if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]+:', value) and not re.match(r'^[a-zA-Z]:[/\\]', value):
        parsed = urlsplit(value)
        if parsed.scheme != 'file' or parsed.netloc not in ('', 'localhost'):
            raise board.BoardError('文档必须是本地项目文件')
        value = unquote(parsed.path)
        if os.name == 'nt' and re.match(r'^/[a-zA-Z]:', value):
            value = value[1:]
    else:
        value = unquote(value)
    value = re.sub(r'#(?:L)?\d+(?:[-:]L?\d+)?$', '', value)
    value = re.sub(r':\d+(?::\d+)?$', '', value)
    return value.replace('\\', '/')


def resolve_path(conn, session_id, text, project=None, must_exist=True, *, historical=False):
    owner, info = _owner(conn, session_id, project)
    roots = [Path(info['root']).expanduser().absolute()]
    if owner.get('worktree'):
        roots.append(Path(owner['worktree']).expanduser().absolute())
    if historical:
        roots = workspace_settings.read_roots(conn, info['name'], session_id)
    cwd = Path(owner.get('cwd') or info['root']).expanduser().resolve()
    if not any(store.is_under(cwd, root) for root in roots):
        cwd = roots[0]
    value = _path_text(text)
    if value.startswith('//'):
        raise board.BoardError('文档不能使用网络或设备路径')
    candidate = Path(value).expanduser()
    relative = not candidate.is_absolute()
    if relative:
        candidate = cwd / candidate
        if not any(store.is_under(Path(os.path.abspath(candidate)), root) for root in roots):
            raise board.BoardError('文档路径不在当前项目或提交者的工作区内')
        if not candidate.exists():
            candidate = roots[0] / value
    candidates = [candidate]
    if historical:
        if relative:
            candidates.extend(root / value for root in roots)
        for change in workspace_settings.directory_changes(conn, info['name']):
            mappings = [(change['old_root'], change['new_root'])]
            for moved in change['sessions']:
                if moved['session_id'] == session_id:
                    mappings.extend((moved.get('old_' + key), moved.get('new_' + key)) for key in ('cwd', 'worktree'))
            for old, new in mappings:
                if old is None or new is None:
                    continue
                for path in tuple(candidates):
                    if store.is_under(path, old):
                        mapped = Path(new) / os.path.relpath(path, old)
                        if all(store.norm_path(mapped) != store.norm_path(item) for item in candidates):
                            candidates.append(mapped)
        candidate = next((path for path in candidates if path.is_file() and
                          any(store.is_under(Path(os.path.abspath(path)), root) for root in roots) and
                          any(store.is_under(path.resolve(), root) for root in roots)), candidate)
    if not any(store.is_under(Path(os.path.abspath(candidate)), root) for root in roots):
        raise board.BoardError('文档路径不在当前项目或提交者的工作区内')
    candidate = candidate.resolve()
    if not any(store.is_under(candidate, root) for root in roots):
        raise board.BoardError('文档路径不在当前项目或提交者的工作区内')
    if candidate.suffix.lower() not in EXTENSIONS:
        raise board.BoardError('此文件类型不能登记为文档')
    if must_exist and not candidate.is_file():
        raise board.BoardError('文档文件不存在：' + str(candidate))
    return candidate


def _leader(conn, owner):
    if owner['role'] == 'leader':
        return owner['session_id']
    row = conn.execute('SELECT leader_session_id FROM groups WHERE project=? AND name=?',
                       (owner['project'], owner['group_name'])).fetchone()
    return row[0] if row is not None else None


def _public(conn, row):
    result = dict(row)
    result['group'] = result.pop('group_name')
    result.pop('path_key', None)
    result['ai_important'] = bool(result['ai_important'])
    result['user_important'] = None if result['user_important'] is None else bool(result['user_important'])
    result['important'] = result['ai_important'] if result['user_important'] is None else result['user_important']
    try:
        target = resolve_path(conn, result['session_id'], result['path'], result['project'], historical=True)
        result['resolved_path'] = str(target)
        result['exists'], result['file_error'] = True, None
    except (board.BoardError, OSError, RuntimeError) as exc:
        result['resolved_path'] = None
        result['exists'], result['file_error'] = False, str(exc)
    return result


def get(conn, project, document_id):
    if type(document_id) is not int or document_id <= 0:
        raise board.BoardError('文档 id 必须是正整数')
    row = conn.execute('SELECT * FROM documents WHERE id=? AND project=?', (document_id, project)).fetchone()
    if row is None:
        raise board.BoardError('文档不属于当前工作区或已不存在')
    return _public(conn, row)


def _goal(conn, owner, goal_id, execution_id, *, live):
    if execution_id is not None:
        execution = goals.get_execution(conn, execution_id)
        if execution['session_id'] != owner['session_id']:
            raise board.BoardError('文档执行与提交者不匹配')
        if goal_id is not None and goal_id != execution['goal_id']:
            raise board.BoardError('文档执行与目标不匹配')
        goal_id = execution['goal_id']
        if live:
            gate = goals.dispatch_check(conn, goal_id, execution_id=execution_id)
            if not gate['allowed']:
                raise board.Conflict(gate['reason'])
    if goal_id is not None:
        goal = goals.get(conn, goal_id, owner['project'])
        if owner['session_id'] != goal['leader_session_id'] and conn.execute(
                'SELECT 1 FROM goal_assignments WHERE goal_id=? AND session_id=?',
                (goal_id, owner['session_id'])).fetchone() is None:
            raise board.BoardError('文档提交者未参与此目标')
    return goal_id


def register(conn, session_id, path, title=None, summary=None, kind='other', important=False, now=None, *,
             project=None, source='manual', source_seq=None, goal_id=None, execution_id=None):
    owner, info = _owner(conn, session_id, project)
    goal_id = _goal(conn, owner, goal_id, execution_id, live=source == 'manual')
    target = resolve_path(conn, session_id, path, info['name'], historical=source != 'manual')
    title = _clean(title, 'title', 300, optional=source!='manual')
    summary = _clean(summary, 'summary', 4000, optional=source!='manual')
    if kind not in KINDS or type(important) is not bool or source not in {'manual', 'journal', 'report'}:
        raise board.BoardError('文档类型或重要标记无效')
    if source_seq is not None and (type(source_seq) is not int or source_seq < 0):
        raise board.BoardError('文档来源序号无效')
    stamp, key = board._time(now), store.norm_path(target)
    with _atomic(conn):
        def submitted(document_id):
            if goal_id is not None:
                conn.execute('INSERT OR IGNORE INTO goal_documents(goal_id,document_id,execution_id,created_at) VALUES(?,?,?,?)',
                             (goal_id, document_id, execution_id, stamp))
            return get(conn, info['name'], document_id)

        old = conn.execute('SELECT * FROM documents WHERE project=? AND path_key=?', (info['name'], key)).fetchone()
        # A historical link is evidence of a file, not authority to rewrite a
        # deliberately submitted title, summary, category, author, or AI flag.
        if old is not None and (source != 'manual' and old['source'] == 'manual'):
            return submitted(old['id']) if old['session_id'] == session_id else _public(conn, old)
        if old is not None and source != 'manual' and old['session_id'] != session_id:
            # Referencing another group's document is not a new submission.
            # Historical mentions cannot move the original card or its author.
            return _public(conn, old)
        if old is None:
            document_id = conn.execute('INSERT INTO documents(project,session_id,leader_session_id,submitter,group_name,path,path_key,title,summary,kind,ai_important,source,source_seq,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (info['name'], session_id, _leader(conn, owner), owner['crew_name'] or session_id, owner['group_name'],
                 str(target), key, title or target.stem, summary, kind, int(important), source, source_seq, stamp, stamp)).lastrowid
        else:
            document_id = old['id']
            if source != 'manual' and stamp < old['updated_at']:
                return submitted(old['id'])
            conn.execute('UPDATE documents SET session_id=?,leader_session_id=?,submitter=?,group_name=?,path=?,title=?,summary=?,kind=?,ai_important=?,source=?,source_seq=?,updated_at=? WHERE id=?',
                (session_id, _leader(conn, owner), owner['crew_name'] or session_id, owner['group_name'], str(target),
                 title or old['title'], summary if summary is not None else old['summary'], kind, int(important), source, source_seq, stamp, document_id))
        if source == 'manual':
            board._event(conn, 'document:register', info['name'], session_id, {'id': document_id, 'path': str(target), 'kind': kind}, stamp)
        return submitted(document_id)


def list(conn, project, leader_session_id=None, *, goal_id=None):
    board.project(conn, project)
    params = [project]
    sql = 'SELECT * FROM documents WHERE project=?'
    if goal_id is not None:
        goals.get(conn, goal_id, project)
        sql += ' AND id IN (SELECT document_id FROM goal_documents WHERE goal_id=?)'
        params.append(goal_id)
    if leader_session_id is not None:
        owner = board.session(conn, leader_session_id)
        if owner['project'] != project or owner['role'] != 'leader':
            raise board.BoardError('文档分组 Leader 不属于当前工作区')
        sql += ' AND leader_session_id=?'
        params.append(leader_session_id)
    sql += " ORDER BY COALESCE(user_important,ai_important) DESC, CASE kind WHEN 'summary' THEN 0 ELSE 1 END, updated_at DESC,id DESC LIMIT 1000"
    return [_public(conn, row) for row in conn.execute(sql, params)]


def pin(conn, project, document_id, important, now=None):
    if important is not None and type(important) is not bool:
        raise board.BoardError('重要标记必须是 true、false 或 null')
    with _atomic(conn):
        get(conn, project, document_id)
        conn.execute('UPDATE documents SET user_important=? WHERE id=? AND project=?', (important, document_id, project))
        board._event(conn, 'document:pin', project, board.HUMAN, {'id': document_id, 'important': important}, board._time(now))
        return get(conn, project, document_id)


def _links(text):
    if not isinstance(text, str):
        return
    seen = set()
    for match in LINK.finditer(text):
        target = match[2] or match[3]
        # Support an optional Markdown quoted tooltip without treating it as a path.
        target = re.sub(r'\s+"[^"\n]*"$', '', target).strip()
        if target not in seen:
            seen.add(target)
            yield match[1].strip(), target
    for match in re.finditer(r'`([^`\n]{1,4096})`', text):
        if match[1] not in seen:
            seen.add(match[1]); yield None, match[1]
    for match in re.finditer(r'''(?<![\w:/])((?:[a-zA-Z]:[/\\]|/|\.{1,2}[/\\]|(?:[\w.-]+[/\\])+)[^\s<>\[\]()"'`]+)''', text):
        target = match[1].rstrip('.,;，。；')
        if target not in seen:
            seen.add(target); yield None, target


def _scan_record(conn, project, source, offset, size, mtime_ns, identity, error, now):
    conn.execute('INSERT INTO document_scans(project,source,offset,size,mtime_ns,identity,error,scanned_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(project,source) DO UPDATE SET offset=excluded.offset,size=excluded.size,mtime_ns=excluded.mtime_ns,identity=excluded.identity,error=excluded.error,scanned_at=excluded.scanned_at',
                 (project, source, offset, size, mtime_ns, identity, error, now))


def refresh(conn, home, project, now=None):
    board.project(conn, project)
    stamp = board._time(now)
    result = {'indexed': 0, 'scanned': 0, 'has_more': False, 'errors': []}
    remaining, lines, scanned_sessions = MAX_BYTES, 0, 0
    def ingest(session_id, text, source, sequence, at, execution_id=None):
        for title, target in _links(text):
            try:
                clean = _path_text(target)
                if Path(clean).suffix.lower() not in AUTO_EXTENSIONS:
                    continue
                label = ((title or '') + ' ' + Path(clean).stem).lower()
                kind = ('summary' if any(word in label for word in ('汇总','总结','summary','overview')) else
                        'report' if any(word in label for word in ('报告','report','验收')) else
                        'guide' if any(word in label for word in ('指南','教程','guide','readme','manual')) else 'other')
                register(conn, session_id, target, title=title, kind=kind, now=at or stamp, project=project,
                         source=source, source_seq=sequence, execution_id=execution_id)
                result['indexed'] += 1
            except (board.BoardError, OSError, RuntimeError):
                # External links, code links, missing files and escaped paths are
                # not documents. File/index I/O failures are surfaced separately.
                continue
    with _atomic(conn):
        sessions = conn.execute("SELECT j.* FROM (SELECT session_id,session_id journal_id,NULL execution_id,project FROM sessions "
                                "UNION ALL SELECT e.session_id,e.id,e.id,g.project FROM goal_executions e JOIN goals g ON g.id=e.goal_id) j "
                                "LEFT JOIN document_scans d ON d.project=j.project AND d.source='journal:'||j.journal_id "
                                "WHERE j.project=? ORDER BY COALESCE(d.scanned_at,''),j.journal_id LIMIT 200", (project,)).fetchall()
        for owner in sessions:
            if scanned_sessions >= MAX_SESSIONS or remaining <= 0 or lines >= MAX_LINES:
                result['has_more'] = True; break
            sid = owner['session_id']; source = 'journal:' + owner['journal_id']
            old = conn.execute('SELECT * FROM document_scans WHERE project=? AND source=?', (project, source)).fetchone()
            offset = old['offset'] if old is not None else 0
            size = mtime = 0; identity = ''; error = None
            try:
                path = Path(home) / 'chat' / (owner['journal_id'] + '.jsonl')
                # Session IDs originate in stored sessions, but never allow them
                # to turn an index scan into reads outside this home/chat folder.
                if path.resolve().parent != (Path(home) / 'chat').resolve():
                    raise ValueError('会话日志路径无效')
                stat = path.stat(); size, mtime = stat.st_size, stat.st_mtime_ns; identity = str(stat.st_ino)
                if old is not None and (size < offset or identity != old['identity'] or size == offset and mtime != old['mtime_ns']):
                    offset = 0
                if size == offset and old is not None and mtime == old['mtime_ns']:
                    _scan_record(conn, project, source, offset, size, mtime, identity, None, stamp)
                    continue
                scanned_sessions += 1
                with path.open('rb') as stream:
                    stream.seek(offset)
                    while remaining > 0 and lines < MAX_LINES:
                        start = stream.tell(); raw = stream.readline(min(MAX_LINE + 1, remaining + 1))
                        if not raw: break
                        if len(raw) > remaining: result['has_more'] = True; break
                        if not raw.endswith(b'\n'):
                            if len(raw) > MAX_LINE:
                                error = '单条会话日志超过索引上限，跳过超长片段'
                                result['errors'].append({'source':source,'error':error})
                                remaining -= len(raw); offset = stream.tell(); lines += 1
                                continue
                            result['has_more'] = size > stream.tell(); break
                        remaining -= len(raw); lines += 1; offset = stream.tell(); result['scanned'] += 1
                        try:
                            event = json.loads(raw.decode('utf-8-sig'))
                        except (UnicodeError, ValueError) as exc:
                            result['errors'].append({'source':source,'error':'日志 JSON 无效：'+str(exc)[:150]}); continue
                        if not isinstance(event, dict) or event.get('type') != 'assistant': continue
                        content = event.get('content', [])
                        if isinstance(content, str): content = [{'type':'text','text':content}]
                        if not isinstance(content, builtins_list): continue
                        for block in content:
                            if isinstance(block, dict) and block.get('type') == 'text':
                                sequence=event.get('seq')
                                sequence=sequence if type(sequence) is int and sequence>=0 else None
                                at=event.get('at')
                                ingest(sid, block.get('text'), 'journal', sequence, at if isinstance(at,str) and len(at)<=40 else stamp,
                                       owner['execution_id'])
                    if offset < size: result['has_more'] = True
            except FileNotFoundError:
                offset = 0
            except (OSError, ValueError, RuntimeError) as exc:
                error = str(exc); result['errors'].append({'source':source,'error':error})
            _scan_record(conn, project, source, offset, size, mtime, identity, error, stamp)
        old = conn.execute("SELECT offset FROM document_scans WHERE project=? AND source='reports'", (project,)).fetchone()
        last_id = old[0] if old is not None else 0
        reports = conn.execute('SELECT id,session_id,created_at,length(CAST(body AS BLOB)) AS bytes FROM reports WHERE project=? AND id>? ORDER BY id LIMIT 201', (project, last_id)).fetchall()
        for report in reports[:200]:
            if remaining <= 0 or lines >= MAX_LINES: result['has_more'] = True; break
            encoded = report['bytes']
            if encoded > MAX_BYTES:
                result['errors'].append({'source':'report:'+str(report['id']),'error':'报告超过历史索引上限，请用 crew doc 显式登记'})
                last_id = report['id']; continue
            if encoded > remaining: result['has_more'] = True; break
            body=conn.execute('SELECT body FROM reports WHERE project=? AND id=?',(project,report['id'])).fetchone()[0]
            remaining -= encoded; lines += 1; result['scanned'] += 1
            ingest(report['session_id'], body, 'report', report['id'], report['created_at']); last_id = report['id']
        if len(reports) > 200: result['has_more'] = True
        _scan_record(conn, project, 'reports', last_id, 0, 0, '', None, stamp)
    return result


# The public list() intentionally follows board/todo conventions; retain the
# concrete type for validating decoded journal content without shadowing it.
from builtins import list as builtins_list
