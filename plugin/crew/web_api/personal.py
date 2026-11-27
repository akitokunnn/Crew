"""Global assistant page state; transport and native process ownership stay out."""
from contextlib import closing

from .. import board, event_sounds, guard, mindmap_api, models, personal, settings, store, workspace_assistant
from . import personal as personal_api
from .common import _text


def open_assistant(home, context_project=None, *, project=None):
    with closing(store.connect(home)) as conn:
        if project is not None:
            return workspace_assistant.ensure(conn, home, project)
        return personal.ensure_assistant(conn, home, context_project=context_project)


def state(home, *, history, journal, activity, applied, context, project=None, context_project=None):
    """Each callback takes one session_id and only reads its managed chat state."""
    with closing(store.connect(home)) as conn:
        assistant = (workspace_assistant.ensure(conn, home, project) if project is not None else
                     personal.ensure_assistant(conn, home, context_project=context_project))
        snapshot = {**personal.overview(conn, project=project), 'scope': assistant['scope']}
        ai = settings.get(conn)
        sounds = {**event_sounds.settings_value(conn, home), 'events': event_sounds.events(conn, project, history, journal)}
    sid = assistant['session_id']
    chat = history(sid)
    events = chat['events']
    assistant.update(
        name=assistant['crew_name'],
        runtime=assistant['runtime']['runtime'], native_id=assistant['runtime']['native_id'],
        **{key: assistant['configuration'][key] for key in ('model', 'effort')},
        running=chat['running'], busy=chat['busy'], activity=activity(sid), applied=applied(sid),
        context=context(sid), last_seq=events[-1]['seq'] if events else 0, web_chat=True,
        where='bg', status='working' if chat['busy'] else 'idle' if chat['running'] else 'offline',
        source='crew', chat_unread=0, goals=[], active_goal_id=None, active_goal_ids=[],
        delegation=[], subagents=[])
    return {'scope': assistant['scope'], 'project': snapshot['projects'][0] if project is not None else None,
            'now': store.now_iso(), 'assistant': assistant,
            'leaders': snapshot['leaders'] if project is not None else [], 'members': [assistant], 'projects': snapshot['projects'],
            'todos': snapshot['todos'], 'todo_count': snapshot['counts']['todos'], 'goals': snapshot['goals'],
            'ai': ai, 'sounds': sounds, 'models': models.MODELS, 'efforts': models.EFFORTS,
            'decisions': [], 'attention': [], 'native_approvals': [], 'agents_error': None,
            'overview': snapshot}


def prepare_send(home, session_id):
    """Configure only on an explicit send; plain opening never probes a CLI."""
    with closing(store.connect(home)) as conn:
        project = workspace_assistant.project_for(conn, session_id)
        if project is not None:
            workspace_assistant.require_identity(conn, session_id, project)
            if conn.execute('SELECT 1 FROM session_runtimes WHERE session_id=?', (session_id,)).fetchone() is None:
                workspace_assistant.ensure(conn, home, project, selection=settings.resolve(conn, 'assistant'))
            return board.session(conn, session_id)
        personal._active_assistant(conn, session_id)
        assistant = personal.ensure_assistant(conn, home)
        if not assistant['configured']:
            assistant = personal.ensure_assistant(conn, home, selection=settings.resolve(conn, 'assistant'))
        return board.session(conn, assistant['session_id'])


def notes(home, *, project=None, include_archived=False):
    if not store.db_path(home).exists():
        return {'notes': [], 'stale_days': personal.STALE_DAYS, 'model_calls': 0}
    # Opening the wall upgrades note storage once; it never starts AI.
    with closing(store.connect(home)) as conn:
        personal.ensure_schema(conn)
        return {'notes': personal.list_notes(conn, project=project, include_archived=include_archived),
                'stale_days': personal.STALE_DAYS, 'model_calls': 0}


def tags(home):
    if not store.db_path(home).exists():
        return {'tags': [], 'palette': list(personal.PALETTE), 'model_calls': 0}
    with closing(store.connect(home)) as conn:
        personal.ensure_schema(conn)
        return {**personal.list_tags(conn), 'model_calls': 0}


def _command(data):
    if not isinstance(data, dict) or set(data) != {'action', 'data', 'request_id'}:
        raise board.BoardError('请求体必须正好包含 action、data、request_id')
    return data['action'], data['data'], data['request_id']


def save_note(home, data):
    action, payload, request_id = _command(data)
    with closing(store.connect(home)) as conn:
        return personal.note_action(conn, action, payload, request_id=request_id)


def save_tag(home, data):
    action, payload, request_id = _command(data)
    with closing(store.connect(home)) as conn:
        return personal.tag_action(conn, action, payload, request_id=request_id)


def note_recovery(home, note_id):
    with closing(guard.readonly(home)) as conn:
        return personal.note_recovery(conn, note_id)


def recover_note(home, data):
    with closing(store.connect(home)) as conn:
        return personal.bind_note_leader(conn, data.get('note_id'), data.get('session_id'),
            request_id=data.get('request_id'), reason=data.get('reason'))


def post_personal_notes(service, path, data, project_name):
    return personal_api.save_note(service.home, data)


def post_personal_tags(service, path, data, project_name):
    return personal_api.save_tag(service.home, data)


def post_personal_notes_recovery(service, path, data, project_name):
    return personal_api.recover_note(service.home, data)


def post_personal_notes_start(service, path, data, project_name):
    return service.chat.start_personal_note(_text(data, 'note_id'), _text(data, 'project'),
                                         request_id=_text(data, 'request_id'))


def post_assistant_open(service, path, data, project_name):
    if 'project' in data and 'context_project' in data:
        raise ValueError('project 与 context_project 不能同传')
    return personal_api.open_assistant(service.home, data.get('context_project'), project=data.get('project'))


def get_personal_notes_recovery(handler, service, url, query):
    handler._send(200, personal_api.note_recovery(service.home, query.get('note_id', [''])[0]))


def get_personal_notes(handler, service, url, query):
    handler._send(200, personal_api.notes(service.home, project=query.get('project', [None])[0],
        include_archived=query.get('include_archived') == ['1']))


def get_personal_tags(handler, service, url, query):
    handler._send(200, personal_api.tags(service.home))


def get_assistant_state(handler, service, url, query):
    if 'project' in query and 'context_project' in query:
        raise ValueError('project 与 context_project 不能同传')
    for key in ('project', 'context_project'):
        if key in query and (len(query[key]) != 1 or not query[key][0].strip()):
            raise ValueError(key + ' 必须是单个非空工作区名')
    handler._send(200, personal_api.state(service.home, project=query.get('project', [None])[0],
        context_project=query.get('context_project', [None])[0], history=service.chat.history, journal=service.chat.journal,
        activity=service.chat.activity, applied=service.chat.applied, context=service.chat.display_context))


def post_personal_mindmap(service, path, data, project_name):
    return mindmap_api.edit(service.home, data)


def post_personal_mindmap_preview(service, path, data, project_name):
    return mindmap_api.preview(service.home, data)


def post_personal_mindmap_convert(service, path, data, project_name):
    # Writes the target workspace's architecture board, so it runs under the service write lock.
    with service.write_lock:
        return mindmap_api.convert(service.home, data)


def get_personal_mindmap(handler, service, url, query):
    handler._send(200, mindmap_api.snapshot(service.home))
