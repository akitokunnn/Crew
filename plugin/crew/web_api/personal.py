"""Global assistant page state; transport and native process ownership stay out."""
from contextlib import closing

from .. import board, guard, models, personal, settings, store


def open_assistant(home, context_project=None):
    with closing(store.connect(home)) as conn:
        return personal.ensure_assistant(conn, home, context_project=context_project)


def state(home, *, history, activity, applied, context):
    """Each callback takes one session_id and only reads its managed chat state."""
    with closing(store.connect(home)) as conn:
        assistant = personal.ensure_assistant(conn, home)
        snapshot = personal.overview(conn)
        ai = settings.get(conn)
    sid = assistant['session_id']
    chat = history(sid)
    events = chat['events']
    assistant.update(
        name=assistant['crew_name'],
        runtime=assistant['runtime']['runtime'], native_id=assistant['runtime']['native_id'],
        **{key: assistant['configuration'][key] for key in ('model', 'effort')},
        running=chat['running'], busy=chat['busy'], activity=activity(sid), applied=applied(sid),
        context=context(sid), last_seq=events[-1]['seq'] if events else 0, web_chat=True,
        native_handoff=chat.get('native_handoff'),
        where='bg', status='working' if chat['busy'] else 'idle' if chat['running'] else 'offline',
        source='crew', chat_unread=0, goals=[], active_goal_id=None, active_goal_ids=[],
        delegation=[], subagents=[])
    return {'scope': 'personal', 'project': None, 'now': store.now_iso(), 'assistant': assistant,
            'leaders': [], 'members': [assistant], 'projects': snapshot['projects'],
            'todos': snapshot['todos'], 'todo_count': snapshot['counts']['todos'], 'goals': snapshot['goals'],
            'ai': ai, 'models': models.MODELS, 'efforts': models.EFFORTS,
            'decisions': [], 'attention': [], 'native_approvals': [], 'agents_error': None,
            'overview': snapshot}


def prepare_send(home, session_id):
    """Configure only on an explicit send; plain opening never probes a CLI."""
    with closing(store.connect(home)) as conn:
        personal._active_assistant(conn, session_id)
        assistant = personal.ensure_assistant(conn, home)
        if not assistant['configured']:
            assistant = personal.ensure_assistant(conn, home, selection=settings.resolve(conn, 'assistant'))
        return board.session(conn, assistant['session_id'])


def notes(home, *, project=None, include_archived=False):
    if not store.db_path(home).exists():
        return {'notes': [], 'model_calls': 0}
    with closing(guard.readonly(home)) as conn:
        return {'notes': personal.list_notes(conn, project=project, include_archived=include_archived), 'model_calls': 0}


def save_note(home, data):
    if not isinstance(data, dict):
        raise board.BoardError('想法操作参数必须为对象')
    payload = {key: value for key, value in data.items() if key not in ('action', 'request_id')}
    with closing(store.connect(home)) as conn:
        return personal.note_action(conn, data.get('action'), payload, request_id=data.get('request_id'))


def note_recovery(home, note_id):
    with closing(guard.readonly(home)) as conn:
        return personal.note_recovery(conn, note_id)


def recover_note(home, data):
    with closing(store.connect(home)) as conn:
        return personal.bind_note_leader(conn, data.get('note_id'), data.get('session_id'),
            request_id=data.get('request_id'), reason=data.get('reason'))
