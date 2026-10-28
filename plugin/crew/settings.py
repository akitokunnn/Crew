"""HOME-wide AI choices. Loading/resolving settings never starts a native CLI."""
from contextlib import closing, contextmanager
import copy
import json
import shutil
import sqlite3
import uuid

from . import board, guard, store


PURPOSES = ('default', 'leader', 'member', 'digest', 'git', 'operations')
TASK_OVERRIDES = ('leader', 'member')
SCOPED_PURPOSES = PURPOSES + ('assistant',)
FIELDS = ('runtime', 'model', 'effort')
ROLE_PREFERENCES = {'leader': 'claude', 'member': 'codex'}
DEFAULT = {'default': {'runtime': None, 'model': None, 'effort': None},
           'leader': None, 'member': None, 'digest': None, 'git': None, 'operations': None}
EXECUTION_DEFAULT = {'codex': 'workspace-write', 'claude': 'default'}
EXECUTION_CHOICES = {'codex': ('read-only', 'workspace-write', 'danger-full-access'),
                     'claude': ('default', 'acceptEdits', 'bypassPermissions')}


class SettingsError(board.BoardError):
    pass


def _selection(value, name):
    if not isinstance(value, dict) or set(value) != set(FIELDS):
        raise SettingsError(name + ' 必须包含 runtime、model、effort 三个字段')
    if value['runtime'] not in (None, 'claude', 'codex'):
        raise SettingsError(name + '.runtime 必须是 null、claude 或 codex')
    for field, limit in (('model', 300), ('effort', 100)):
        text = value[field]
        if text is not None and (not isinstance(text, str) or not text.strip() or text != text.strip()
                                 or len(text) > limit or text.startswith('-')
                                 or any(ord(char) < 32 or ord(char) == 127 for char in text)):
            raise SettingsError(name + '.' + field + ' 必须是非空有效文本或 null')
    return dict(value)


def validate(ai):
    """A null task follows default; an explicit task replaces all three fields."""
    if not isinstance(ai, dict) or 'default' not in ai or any(key not in SCOPED_PURPOSES for key in ai):
        raise SettingsError('AI 设置必须包含 default，且只能包含 default、leader、member、digest、git、operations')
    result = {'default': _selection(ai['default'], 'default')}
    for purpose in PURPOSES[1:]:
        value = ai.get(purpose)
        result[purpose] = None if value is None else _selection(value, purpose)
    if 'assistant' in ai:
        result['assistant'] = None if ai['assistant'] is None else _selection(ai['assistant'], 'assistant')
    return result


def _load(conn):
    # A pre-settings database has no choice yet; reads do not run migrations.
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_settings'").fetchone():
        return copy.deepcopy(DEFAULT)
    row = conn.execute('SELECT ai_json FROM ai_settings WHERE singleton=1').fetchone()
    value = copy.deepcopy(DEFAULT) if row is None else validate(json.loads(row[0]))
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_task_settings'").fetchone():
        for purpose, selection_json in conn.execute('SELECT purpose,selection_json FROM ai_task_settings'):
            if purpose in TASK_OVERRIDES:
                value[purpose] = _selection(json.loads(selection_json), purpose)
    return value


def get(source=None):
    """Read a HOME path or the caller's connection; None means unconfigured."""
    if isinstance(source, sqlite3.Connection):
        return _load(source)
    if source is None or not store.db_path(source).exists():
        return copy.deepcopy(DEFAULT)
    with closing(guard.readonly(source)) as conn:
        return _load(conn)


def load(conn):
    return get(conn)


def _execution(value):
    if not isinstance(value, dict) or set(value) != set(EXECUTION_CHOICES):
        raise SettingsError('执行权限设置必须且只能包含 codex、claude')
    for runtime, choices in EXECUTION_CHOICES.items():
        if value[runtime] not in choices:
            raise SettingsError(runtime + ' 执行权限必须是 ' + '、'.join(choices))
    return dict(value)


def _load_execution(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_settings'").fetchone():
        return dict(EXECUTION_DEFAULT)
    row = conn.execute('SELECT codex,claude FROM execution_settings WHERE singleton=1').fetchone()
    return dict(EXECUTION_DEFAULT) if row is None else _execution({'codex': row[0], 'claude': row[1]})


def get_execution(source=None):
    """Read launch/resume permission defaults without changing any live process."""
    if isinstance(source, sqlite3.Connection):
        return _load_execution(source)
    if source is None or not store.db_path(source).exists():
        return dict(EXECUTION_DEFAULT)
    with closing(guard.readonly(source)) as conn:
        return _load_execution(conn)


def save_execution(source, value):
    """Save for subsequent launch/resume only; current processes are not restarted."""
    value = _execution(value)
    if source is None:
        raise SettingsError('保存执行权限必须指定 HOME 或数据库连接')
    if isinstance(source, sqlite3.Connection):
        with scoped_transaction(source):
            source.execute('INSERT INTO execution_settings(singleton,codex,claude,updated_at) VALUES(1,?,?,?) '
                           'ON CONFLICT(singleton) DO UPDATE SET codex=excluded.codex,claude=excluded.claude,updated_at=excluded.updated_at',
                           (value['codex'], value['claude'], store.now_iso()))
            _bump_global(source)
    else:
        with closing(store.connect(source)) as conn:
            save_execution(conn, value)
    return value


def save(source, ai):
    """Save one complete AI object; a caller-owned transaction stays caller-owned."""
    value = validate(ai)
    if source is None:
        raise SettingsError('保存 AI 设置必须指定 HOME 或数据库连接')
    if isinstance(source, sqlite3.Connection):
        with scoped_transaction(source):
            # Keep existing readers of ai_json working while a desktop reconnects to
            # an older backend; the new purpose has exactly one separate owner.
            existing = {key: item for key, item in value.items() if key not in TASK_OVERRIDES}
            source.execute('INSERT INTO ai_settings(singleton,ai_json,updated_at) VALUES(1,?,?) '
                           'ON CONFLICT(singleton) DO UPDATE SET ai_json=excluded.ai_json,updated_at=excluded.updated_at',
                           (json.dumps(existing, ensure_ascii=False, allow_nan=False), store.now_iso()))
            for purpose in TASK_OVERRIDES:
                if value[purpose] is None:
                    source.execute('DELETE FROM ai_task_settings WHERE purpose=?', (purpose,))
                else:
                    source.execute('INSERT INTO ai_task_settings(purpose,selection_json,updated_at) VALUES(?,?,?) '
                                   'ON CONFLICT(purpose) DO UPDATE SET selection_json=excluded.selection_json,updated_at=excluded.updated_at',
                                   (purpose, json.dumps(value[purpose], ensure_ascii=False, allow_nan=False), store.now_iso()))
            _bump_global(source)
    else:
        with closing(store.connect(source)) as conn:
            save(conn, value)
    return value


def resolve(source, purpose='default', which=None, *, project=None, session_id=None):
    """Select one whole choice, including project/session overrides when requested."""
    if purpose not in SCOPED_PURPOSES:
        raise SettingsError('未知 AI 设置用途: ' + str(purpose))
    configured = effective(source, purpose=purpose, project=project, session_id=session_id)
    if configured['role_preference'] is not None:
        which = shutil.which if which is None else which
        preferred = configured['role_preference']['runtime']
        available = [name for name in ('claude', 'codex') if which(name)]
        if not available:
            raise SettingsError('请先安装并登录 Codex CLI 或 Claude Code，再启动 AI 功能')
        selected = preferred if preferred in available else available[0]
        fallback = selected != preferred
        reason = ('未找到角色首选 %s；使用实际可用的 %s' % (preferred, selected)) if fallback else '未显式选择载体；使用角色首选 ' + selected
        return {'runtime': selected, 'model': None, 'effort': None, 'resolution': {
            'source': 'builtin-role-preference', 'preferred_runtime': preferred, 'selected_runtime': selected,
            'available': available, 'fallback': fallback, 'reason': reason,
            'applies': 'next_launch', 'loaded': 'not_confirmed'}}
    return resolve_choice(configured['ai'], which)


def resolve_choice(value, which=None):
    """Resolve one whole selection; an explicit carrier never silently switches."""
    selected = dict(value)
    which = shutil.which if which is None else which
    runtime = selected['runtime']
    if runtime is not None:
        if not which(runtime):
            raise SettingsError('AI 设置已选择 %s，但未找到该载体；请安装并登录，或在设置页改选' % runtime)
    else:
        available = [name for name in ('claude', 'codex') if which(name)]
        if not available:
            raise SettingsError('请先安装并登录 Codex CLI 或 Claude Code，再启动 AI 功能')
        if len(available) != 1:
            raise SettingsError('检测到 Codex CLI 和 Claude Code；请先在设置页选择默认载体或本任务载体')
        runtime = available[0]
    selected['runtime'] = runtime
    return selected


@contextmanager
def scoped_transaction(conn):
    """Keep scope updates atomic without committing the caller's transaction."""
    if not conn.in_transaction:
        with board.transaction(conn):
            yield
        return
    name = 'settings_' + uuid.uuid4().hex
    conn.execute('SAVEPOINT ' + name)
    try:
        yield
        conn.execute('RELEASE ' + name)
    except BaseException:
        conn.execute('ROLLBACK TO ' + name)
        conn.execute('RELEASE ' + name)
        raise


def ensure_schema(conn):
    """Add controls to crew.db; never opens another database or starts a tool."""
    conn.execute("CREATE TABLE IF NOT EXISTS settings_scopes ("
                 "scope TEXT NOT NULL CHECK(scope IN ('global','project','session')),"
                 "identity TEXT NOT NULL,ai_json TEXT NOT NULL DEFAULT '{}',"
                 "execution_json TEXT NOT NULL DEFAULT '{}',revision INTEGER NOT NULL DEFAULT 1,"
                 "updated_at TEXT NOT NULL,PRIMARY KEY(scope,identity))")


def _has_scopes(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings_scopes'").fetchone() is not None


def _bump_global(conn):
    # Existing settings.save/save_execution must also invalidate scope CAS tokens.
    if _has_scopes(conn):
        conn.execute("INSERT INTO settings_scopes(scope,identity,updated_at) VALUES('global','global',?) "
                     "ON CONFLICT(scope,identity) DO UPDATE SET revision=revision+1,updated_at=excluded.updated_at",
                     (store.now_iso(),))


def scope_identity(conn, scope, identity=None):
    """Resolve scope ownership before reading or mutating overrides."""
    if scope == 'global':
        if identity not in (None, 'global'):
            raise SettingsError('全局设置不能指定其他归属')
        return 'global'
    if scope in ('project', 'session') and (not isinstance(identity, str) or not identity.strip() or len(identity) > 300):
        raise SettingsError('工作区或 AI 设置必须指定有效归属')
    if scope == 'project':
        board.project(conn, identity)
        return identity
    if scope == 'session':
        board.session(conn, identity)
        return identity
    raise SettingsError('设置作用范围必须是 global、project 或 session')


def _scope_row(conn, scope, identity):
    row = conn.execute('SELECT * FROM settings_scopes WHERE scope=? AND identity=?',
                       (scope, identity)).fetchone() if _has_scopes(conn) else None
    return {'scope': scope, 'identity': identity, 'ai': {}, 'execution': {}, 'version': 0, 'updated_at': None} if row is None else {
        'scope': scope, 'identity': identity, 'ai': json.loads(row['ai_json']),
        'execution': json.loads(row['execution_json']), 'version': row['revision'], 'updated_at': row['updated_at']}


def get_scope(conn, scope='global', identity=None):
    identity = scope_identity(conn, scope, identity)
    value = _scope_row(conn, scope, identity)
    if scope == 'global':
        value.update(ai=get(conn), execution=get_execution(conn))
    return {**value, 'applies': 'next_launch', 'current_execution_changed': False}


def _scope_ai(value):
    if not isinstance(value, dict) or any(key not in SCOPED_PURPOSES for key in value):
        raise SettingsError('AI 覆盖必须按已知用途提供对象')
    return {key: None if item is None else _selection(item, key) for key, item in value.items()}


def _scope_execution(value):
    if not isinstance(value, dict) or any(key not in EXECUTION_CHOICES for key in value):
        raise SettingsError('执行权限覆盖只能包含 codex、claude')
    for runtime, mode in value.items():
        if mode is not None and mode not in EXECUTION_CHOICES[runtime]:
            raise SettingsError(runtime + ' 执行权限必须是 ' + '、'.join(EXECUTION_CHOICES[runtime]))
    return dict(value)


def save_scope(conn, scope='global', identity=None, *, ai=None, execution=None,
               expected_version=None, reset=False):
    """Patch overrides; null removes an override, selections replace all three fields."""
    identity = scope_identity(conn, scope, identity)
    if type(expected_version) is not int or expected_version < 0:
        raise SettingsError('保存设置必须提供非负整数 expected_version')
    if type(reset) is not bool or reset and (ai is not None or execution is not None):
        raise SettingsError('恢复默认不能同时提供覆盖内容')
    if not reset and ai is None and execution is None:
        raise SettingsError('请提供 AI 或执行权限覆盖')
    ai = None if ai is None else _scope_ai(ai)
    execution = None if execution is None else _scope_execution(execution)
    ensure_schema(conn)
    with scoped_transaction(conn):
        old = get_scope(conn, scope, identity)
        if old['version'] != expected_version:
            raise board.Conflict('设置已变化，请保留修改并重新检查当前版本')
        next_ai = copy.deepcopy(DEFAULT) if reset and scope == 'global' else {} if reset else dict(old['ai'])
        next_execution = dict(EXECUTION_DEFAULT) if reset and scope == 'global' else {} if reset else dict(old['execution'])
        for key, value in (ai or {}).items():
            if value is None:
                if scope == 'global':
                    next_ai[key] = dict(DEFAULT['default']) if key == 'default' else None
                else:
                    next_ai.pop(key, None)
            else:
                next_ai[key] = value
        for key, value in (execution or {}).items():
            if value is None:
                if scope == 'global':
                    next_execution[key] = EXECUTION_DEFAULT[key]
                else:
                    next_execution.pop(key, None)
            else:
                next_execution[key] = value
        if scope == 'global':
            # The original tables remain the sole global source of truth.
            save(conn, next_ai)
            save_execution(conn, next_execution)
        stamp = store.now_iso()
        conn.execute('INSERT INTO settings_scopes(scope,identity,ai_json,execution_json,revision,updated_at) '
                     'VALUES(?,?,?,?,?,?) ON CONFLICT(scope,identity) DO UPDATE SET '
                     'ai_json=excluded.ai_json,execution_json=excluded.execution_json,'
                     'revision=excluded.revision,updated_at=excluded.updated_at',
                     (scope, identity, json.dumps({} if scope == 'global' else next_ai, ensure_ascii=False),
                      json.dumps({} if scope == 'global' else next_execution, ensure_ascii=False),
                      expected_version + 1, stamp))
    return get_scope(conn, scope, identity)


def _role_preference(purpose, selected):
    if purpose not in ROLE_PREFERENCES or any(selected[field] is not None for field in FIELDS):
        return None
    return {'runtime': ROLE_PREFERENCES[purpose], 'model': None, 'effort': None,
            'source': 'builtin-role-preference', 'availability': 'not_inspected',
            'loaded': 'not_confirmed', 'applies': 'next_launch'}


def _effective(conn, purpose, project, session_id):
    if purpose not in SCOPED_PURPOSES:
        raise SettingsError('未知 AI 设置用途: ' + str(purpose))
    if session_id is not None:
        session = board.session(conn, session_id)
        if project is not None and session['project'] != project:
            raise SettingsError('AI 不属于指定工作区')
        project = session['project']
    if project is not None:
        board.project(conn, project)
    global_ai = get(conn)
    selected = dict(global_ai['default'] if purpose == 'default' or global_ai.get(purpose) is None else global_ai[purpose])
    global_purpose = purpose if purpose != 'default' and global_ai.get(purpose) is not None else 'default'
    sources = {key: {'scope': 'global', 'identity': 'global', 'purpose': global_purpose} for key in FIELDS}
    execution = get_execution(conn)
    execution_sources = {key: {'scope': 'global', 'identity': 'global'} for key in EXECUTION_CHOICES}
    scopes = [get_scope(conn)]
    for scope, identity in (('project', project), ('session', session_id)):
        if identity is None:
            continue
        value = get_scope(conn, scope, identity)
        scopes.append(value)
        choices = value['ai']
        chosen_purpose = purpose if choices.get(purpose) is not None else 'default'
        if choices.get(chosen_purpose) is not None:
            selected = dict(choices[chosen_purpose])
            sources = {key: {'scope': scope, 'identity': identity, 'purpose': chosen_purpose} for key in FIELDS}
        for runtime, mode in value['execution'].items():
            execution[runtime] = mode
            execution_sources[runtime] = {'scope': scope, 'identity': identity}
    preference = _role_preference(purpose, selected)
    if preference is not None:
        sources = {key: {'scope': 'builtin-role-preference', 'identity': purpose, 'purpose': purpose} for key in FIELDS}
    return {'ai': selected, 'role_preference': preference, 'execution': execution, 'sources': {'ai': sources, 'execution': execution_sources},
            'purpose': purpose, 'project': project, 'session_id': session_id, 'scopes': scopes,
            'applies': 'next_launch', 'current_execution_changed': False,
            'native_configuration': {'status': 'not_inspected', 'loaded': 'not_confirmed'}}


def effective(source, *, purpose='default', project=None, session_id=None):
    """Explain effective values without model discovery, execution or schema writes."""
    if isinstance(source, sqlite3.Connection):
        return _effective(source, purpose, project, session_id)
    if source is None or not store.db_path(source).exists():
        if project is not None or session_id is not None:
            raise SettingsError('工作区或 AI 尚未登记')
        if purpose not in SCOPED_PURPOSES:
            raise SettingsError('未知 AI 设置用途: ' + str(purpose))
        preference = _role_preference(purpose, DEFAULT['default'])
        source = {'scope': 'builtin-role-preference', 'identity': purpose, 'purpose': purpose} if preference is not None else {'scope': 'global', 'identity': 'global', 'purpose': 'default'}
        return {'ai': dict(DEFAULT['default']), 'role_preference': preference, 'execution': dict(EXECUTION_DEFAULT),
                'sources': {'ai': {key: dict(source) for key in FIELDS},
                            'execution': {key: {'scope': 'global', 'identity': 'global'} for key in EXECUTION_CHOICES}},
                'purpose': purpose, 'project': None, 'session_id': None, 'scopes': [], 'applies': 'next_launch',
                'current_execution_changed': False, 'native_configuration': {'status': 'not_inspected', 'loaded': 'not_confirmed'}}
    with closing(guard.readonly(source)) as conn:
        return _effective(conn, purpose, project, session_id)
