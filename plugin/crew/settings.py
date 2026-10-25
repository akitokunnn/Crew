"""HOME-wide AI choices. Loading/resolving settings never starts a native CLI."""
from contextlib import closing, nullcontext
import copy
import json
import shutil
import sqlite3

from . import board, guard, store


PURPOSES = ('default', 'leader', 'digest', 'git', 'operations')
FIELDS = ('runtime', 'model', 'effort')
DEFAULT = {'default': {'runtime': None, 'model': None, 'effort': None},
           'leader': None, 'digest': None, 'git': None, 'operations': None}
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
    if not isinstance(ai, dict) or 'default' not in ai or any(key not in PURPOSES for key in ai):
        raise SettingsError('AI 设置必须包含 default，且只能包含 default、leader、digest、git、operations')
    result = {'default': _selection(ai['default'], 'default')}
    for purpose in PURPOSES[1:]:
        value = ai.get(purpose)
        result[purpose] = None if value is None else _selection(value, purpose)
    return result


def _load(conn):
    # A pre-settings database has no choice yet; reads do not run migrations.
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_settings'").fetchone():
        return copy.deepcopy(DEFAULT)
    row = conn.execute('SELECT ai_json FROM ai_settings WHERE singleton=1').fetchone()
    value = copy.deepcopy(DEFAULT) if row is None else validate(json.loads(row[0]))
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_task_settings'").fetchone():
        leader = conn.execute("SELECT selection_json FROM ai_task_settings WHERE purpose='leader'").fetchone()
        if leader is not None:
            value['leader'] = _selection(json.loads(leader[0]), 'leader')
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
        source.execute('INSERT INTO execution_settings(singleton,codex,claude,updated_at) VALUES(1,?,?,?) '
                       'ON CONFLICT(singleton) DO UPDATE SET codex=excluded.codex,claude=excluded.claude,updated_at=excluded.updated_at',
                       (value['codex'], value['claude'], store.now_iso()))
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
        with (nullcontext() if source.in_transaction else board.transaction(source)):
            # Keep existing readers of ai_json working while a desktop reconnects to
            # an older backend; the new purpose has exactly one separate owner.
            existing = {key: item for key, item in value.items() if key != 'leader'}
            source.execute('INSERT INTO ai_settings(singleton,ai_json,updated_at) VALUES(1,?,?) '
                           'ON CONFLICT(singleton) DO UPDATE SET ai_json=excluded.ai_json,updated_at=excluded.updated_at',
                           (json.dumps(existing, ensure_ascii=False, allow_nan=False), store.now_iso()))
            if value['leader'] is None:
                source.execute("DELETE FROM ai_task_settings WHERE purpose='leader'")
            else:
                source.execute("INSERT INTO ai_task_settings(purpose,selection_json,updated_at) VALUES('leader',?,?) "
                               'ON CONFLICT(purpose) DO UPDATE SET selection_json=excluded.selection_json,updated_at=excluded.updated_at',
                               (json.dumps(value['leader'], ensure_ascii=False, allow_nan=False), store.now_iso()))
    else:
        with closing(store.connect(source)) as conn:
            save(conn, value)
    return value


def resolve(source, purpose='default', which=None):
    """Select runtime/model/effort without switching an explicit carrier."""
    if purpose not in PURPOSES:
        raise SettingsError('未知 AI 设置用途: ' + str(purpose))
    ai = get(source)
    selected = dict(ai['default'] if purpose == 'default' or ai[purpose] is None else ai[purpose])
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
