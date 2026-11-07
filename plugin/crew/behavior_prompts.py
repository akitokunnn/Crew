"""Editable Crew instructions and truthful per-execution delivery manifests.

These are host-injected instructions. They do not expose or replace a carrier's
private system prompt, skills, MCP discovery or native project configuration.
"""
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
import hashlib
import re
import json
import uuid

from . import board, settings, store


BUILTIN_ROOT = Path(__file__).resolve().parents[1]


class Spec:
    """One editable prompt: where its default lives and how the host uses it."""
    __slots__ = ('key', 'label', 'group', 'purpose', 'receivers', 'timing', 'placeholders', 'path', 'max_length')

    def __init__(self, key, label, group, purpose, receivers, timing, placeholders=(), path=None, max_length=100000):
        self.key, self.label, self.group, self.purpose = key, label, group, purpose
        self.receivers, self.timing, self.placeholders = receivers, timing, tuple(placeholders)
        self.path = path or 'prompts/%s.md' % key
        self.max_length = max_length

    def describe(self):
        return {'group': self.group, 'group_label': GROUPS[self.group], 'purpose': self.purpose,
                'receivers': self.receivers, 'timing': self.timing, 'placeholders': list(self.placeholders),
                'file': 'plugin/' + self.path, 'max_length': self.max_length}


GROUPS = {'roles': '角色守则', 'shared': '共用规则', 'delivery': '任务与交付', 'authority': '授权与决策模式',
          'host': '启动、续接与交接', 'helper': '辅助对话与生成'}
_BUNDLED = '随 Crew 行为配置注入：启动时，以及配置变化后的下一次正常输入'
SPECS = (
    Spec('leader', 'Leader 守则', 'roles', 'leader 的职责、建任务与分配、成员管理和授权；共用规则不在这里重复。',
         'CC / Codex · leader', _BUNDLED, path='skills/leader/SKILL.md'),
    Spec('member', '成员守则', 'roles', '成员的边界、授权继承和向 leader 提供的交付材料。',
         'CC / Codex · 成员', _BUNDLED, path='skills/member/SKILL.md'),
    Spec('member_boundary', '成员边界', 'roles', '成员不能再委派、不能创建成员或调用原生子代理；与原生工具限制配套。',
         'CC / Codex · 成员', _BUNDLED),
    Spec('git', 'Git 助手守则', 'roles', 'Git 助手的分支规则、权限、合并请求处理和工作区边界。',
         'CC / Codex · Git 助手', _BUNDLED, path='skills/git/SKILL.md'),
    Spec('assistant', '全局助手守则', 'roles', '跨工作区个人助手的查询、写动作、想法墙与授权边界。',
         'CC / Codex · 全局助手', _BUNDLED, path='skills/assistant/SKILL.md'),
    Spec('common', '通用工作原则', 'shared', '所有角色共用的授权、证据、推进和文档维护原则。',
         'CC / Codex · 全部角色', _BUNDLED),
    Spec('collaboration', '协作与消息', 'shared', '回复用户、Git 请求、组内消息类型与回执、跨组协作、等待和守卫。',
         'CC / Codex · leader、成员、Git 助手', _BUNDLED),
    Spec('human_input', '拍板与人工待办', 'shared', '何时登记拍板、如何登记用户亲手操作以及关联同一人工操作。',
         'CC / Codex · leader、成员、Git 助手', _BUNDLED),
    Spec('context', '上下文与交接', 'shared', '装载范围、按需检索、压缩与换载体交接。',
         'CC / Codex · 全部角色', _BUNDLED),
    Spec('goal_protocol', '目标执行协议', 'delivery', '目标执行的工作方式：只处理当前目标、补充需求、AI 工作清单和轮次边界。',
         'CC / Codex · 目标执行（leader、成员）', '每轮目标输入时作为“最小执行指令”装载', max_length=2000),
    Spec('execution_identity', '目标执行身份', 'delivery', '目标执行进程的身份声明。',
         'CC / Codex · 目标执行', '目标执行进程启动时', ('crew_name', 'session_id')),
    Spec('goal_discussion', '交付后讨论', 'delivery', '已交付任务里只回答问题、需要修改时先登记 amend 的约束。',
         'CC / Codex · 交付后讨论执行', '交付后讨论的每轮输入'),
    Spec('report', '阶段报告', 'delivery', '持续更新的阶段报告、阶段发布和成果版本快照。',
         'CC / Codex · 全部角色', _BUNDLED),
    Spec('final_report', '最终交付报告', 'delivery', '最终 HTML 报告的写法、预览、验收记录和提交，是唯一的最终交付规则。',
         'CC / Codex · leader、Git 助手', _BUNDLED),
    Spec('finish_reminder', '收尾检查提醒', 'delivery', 'leader 一轮结束但任务仍在推进中、又没有提交收尾时，提醒它 finish 或说明在等什么。',
         'CC / Codex · leader 目标执行', '任务停在推进中且本轮结束后，每轮最多一次', ('title',)),
    Spec('rejection_input', '需修改意见转入', 'delivery', '用户在验收时点“需修改”，把意见直接送进本任务执行时的说明。',
         'CC / Codex · leader 目标执行', '用户点“需修改”时', ('note',)),
    Spec('trust_on', '信任模式：开启', 'authority', 'leader 开启信任模式后，本组围绕用户目标自主取舍、实施和验收的说明。',
         'CC / Codex · 本组 leader 与成员', '信任设置变化后的下一次输入', ('goal',)),
    Spec('trust_leader', '信任模式：leader 委派', 'authority', '信任开启时只发给 leader 本人：可为自己的任务选择自主推进。',
         'CC / Codex · leader', '信任设置变化后的下一次输入'),
    Spec('trust_off', '信任模式：关闭', 'authority', '信任关闭后恢复普通拍板流程，不沿用旧授权。',
         'CC / Codex · 本组 leader 与成员', '信任设置变化后的下一次输入', ('goal',)),
    Spec('goal_autonomous', '任务模式：AI 自主决策', 'authority', '单个任务设为自主推进时的范围与约束。',
         'CC / Codex · 目标执行', '任务模式变化后的下一轮目标输入'),
    Spec('goal_human', '任务模式：用户拍板', 'authority', '单个任务设为用户拍板时的约束。',
         'CC / Codex · 目标执行', '任务模式变化后的下一轮目标输入'),
    Spec('codex_session', 'Codex 会话说明', 'host', 'Codex 会话的身份声明及 Claude 工具名到 Codex 工具的对应。',
         'Codex · 总对话', 'Codex 会话启动时，位于守则之前', ('crew_name', 'role', 'project', 'session_id')),
    Spec('resume_nudge', '续跑提示', 'host', '服务重启、运维恢复、用户恢复或载体意外退出后续接未完成工作的统一提示。',
         'CC / Codex · 总对话与目标执行', '需要续接时作为一条输入', ('reason',)),
    Spec('switch_handoff', '换载体交接开头', 'host', '切换 CC / Codex 后交接材料的开头说明。',
         'CC / Codex', '换载体后的第一条输入（用户可在预览里再改）'),
    Spec('lost_native_handoff', '会话丢失交接开头', 'host', '原生会话丢失、改用新会话时交接材料的开头说明。',
         'CC / Codex', '新会话的第一条输入'),
    Spec('codex_desktop', 'Codex 桌面交接', 'host', '把会话交给 Codex 原生桌面继续时替换的说明；成员另附成员边界。',
         'Codex 桌面', '交给 Codex 桌面时'),
    Spec('git_task', 'Git 助手开工任务', 'host', '启用 Git 管理时交给 Git 助手的第一项任务。',
         'CC / Codex · Git 助手', '启用 Git 管理、创建 Git 助手时', ('policy_command', 'requests_command')),
    Spec('role_reminder', '守则更新提醒', 'host', '内置守则文件更新后提醒 CC 会话重读。',
         'CC · 未覆盖守则的会话', '守则文件变化后的下一次输入', ('role',)),
    Spec('workspace_moved', '工作区迁移提示', 'host', '工作区目录迁移后说明新工作目录和旧路径的权限。',
         'CC / Codex', '目录迁移后的会话启动时', ('cwd',)),
    Spec('operations_chat', '运维助手', 'helper', '后台与运维页的诊断对话；诊断快照和最近对话由宿主附在后面。',
         '辅助调用 · 运维助手', '每次运维对话'),
    Spec('idea_discussion', '想法讨论', 'helper', '在想法墙点“去聊聊”时附给全局助手的说明；想法内容填入 {{note}}。',
         'CC / Codex · 全局助手', '从想法墙发起讨论时', ('note',)),
    Spec('summary', '频道摘要', 'helper', '把交流区新发言整理进累积要点卡。',
         '辅助调用 · 摘要模型', '交流区摘要更新时'),
)
SPEC = {spec.key: spec for spec in SPECS}
LABELS = {spec.key: spec.label for spec in SPECS}
KEYS = tuple(SPEC)
ROLE_KEYS = ('leader', 'member', 'assistant', 'git')
BUNDLES = {
    'leader': ('common', 'leader', 'collaboration', 'human_input', 'context', 'report', 'final_report'),
    'member': ('common', 'member', 'member_boundary', 'collaboration', 'human_input', 'context', 'report'),
    'git': ('common', 'git', 'collaboration', 'human_input', 'context', 'report', 'final_report'),
    'assistant': ('common', 'assistant', 'context'),
}
_PLACEHOLDER = re.compile(r'\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}')


def _read_default(key, plugin_root=None):
    return (Path(plugin_root or BUILTIN_ROOT) / SPEC[key].path).read_text(encoding='utf-8')


class _Defaults(Mapping):
    """Built-in texts, read on first use so one missing file cannot stop the whole backend."""

    def __init__(self):
        self._cache = {}

    def __getitem__(self, key):
        if key not in SPEC:
            raise KeyError(key)
        if key not in self._cache:
            self._cache[key] = _read_default(key)
        return self._cache[key]

    def __iter__(self):
        return iter(KEYS)

    def __len__(self):
        return len(KEYS)


DEFAULTS = _Defaults()


def bundle(role):
    if role not in BUNDLES:
        raise settings.SettingsError('未知 Crew 角色: ' + str(role))
    return BUNDLES[role]


def placeholders(text):
    return {match.group(1) for match in _PLACEHOLDER.finditer(text)}


def fill(key, text, values=None):
    """Substitute only declared placeholders; unknown names were rejected on save."""
    values = values or {}
    allowed = set(SPEC[key].placeholders)
    missing = allowed - set(values)
    if placeholders(text) & missing:
        raise settings.SettingsError('提示词 %s 缺少占位符值: %s' % (key, ', '.join(sorted(placeholders(text) & missing))))
    return _PLACEHOLDER.sub(lambda match: str(values[match.group(1)]) if match.group(1) in allowed else match.group(0), text)


def text(conn, key, *, project=None, session_id=None, values=None, plugin_root=None):
    """Effective prompt text for one key, with overrides and placeholders applied."""
    item = resolve(conn, project=project, session_id=session_id, keys=(key,), plugin_root=plugin_root)['prompts'][0]
    return fill(key, item['content'], values)


def desktop_instructions(conn, session_id, role):
    """Codex desktop handoff text; members also keep their delegation boundary."""
    value = text(conn, 'codex_desktop', session_id=session_id).rstrip('\n')
    if role == 'member':
        value += '\n' + text(conn, 'member_boundary', session_id=session_id).rstrip('\n')
    return value


def default_desktop_instructions(role):
    value = default_text('codex_desktop').rstrip('\n')
    return value + ('\n' + default_text('member_boundary').rstrip('\n') if role == 'member' else '')


def global_text(home, key, values=None):
    """Global effective text for diagnostics that must still answer when the board is unreadable.

    The operations assistant exists for a broken HOME; an unreadable database
    falls back to the built-in text instead of blocking diagnosis.
    """
    import sqlite3
    from . import guard
    try:
        conn = guard.readonly(home)
        try:
            return text(conn, key, values=values)
        finally:
            conn.close()
    except sqlite3.Error:
        return default_text(key, values)


def default_text(key, values=None, plugin_root=None):
    """Built-in text without a database, for hosts that have not opened one yet."""
    return fill(key, _read_default(_key(key), plugin_root), values)
DELIVERY_STATES = ('prepared', 'sent', 'confirmed')


@contextmanager
def transaction(conn):
    if not conn.in_transaction:
        with board.transaction(conn):
            yield
        return
    name = 'prompts_' + uuid.uuid4().hex
    conn.execute('SAVEPOINT ' + name)
    try:
        yield
        conn.execute('RELEASE ' + name)
    except BaseException:
        conn.execute('ROLLBACK TO ' + name)
        conn.execute('RELEASE ' + name)
        raise


def scope_identity(conn, scope, identity=None):
    if scope == 'global':
        if identity not in (None, 'global'):
            raise settings.SettingsError('全局提示不能指定其他归属')
        return 'global'
    if scope not in ('project', 'session') or not isinstance(identity, str) or not identity.strip():
        raise settings.SettingsError('提示作用范围必须为 global、project 或 session，并明确归属')
    if scope == 'project':
        board.project(conn, identity)
    else:
        board.session(conn, identity)
    return identity


def for_session(conn, session_id, *, plugin_root=None):
    from . import rules
    session = board.session(conn, session_id)
    role = rules.skill_role(conn, session)
    return resolve(conn, session_id=session_id, keys=bundle(role), plugin_root=plugin_root)


def ensure_schema(conn):
    statements = (
        "CREATE TABLE IF NOT EXISTS behavior_prompts (scope TEXT NOT NULL,identity TEXT NOT NULL,"
        "prompt_key TEXT NOT NULL,content TEXT,version INTEGER NOT NULL,updated_at TEXT NOT NULL,"
        "PRIMARY KEY(scope,identity,prompt_key))",
        "CREATE TABLE IF NOT EXISTS behavior_prompt_history (scope TEXT NOT NULL,identity TEXT NOT NULL,"
        "prompt_key TEXT NOT NULL,version INTEGER NOT NULL,content TEXT,effective_content TEXT NOT NULL,"
        "action TEXT NOT NULL CHECK(action IN ('save','reset')),actor TEXT NOT NULL,created_at TEXT NOT NULL,"
        "PRIMARY KEY(scope,identity,prompt_key,version))",
        "CREATE TABLE IF NOT EXISTS behavior_prompt_applications (id TEXT PRIMARY KEY,execution_id TEXT NOT NULL,"
        "session_id TEXT,project TEXT,manifest_json TEXT NOT NULL,manifest_hash TEXT NOT NULL,"
        "status TEXT NOT NULL CHECK(status IN ('prepared','sent','confirmed')),evidence TEXT,created_at TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS behavior_prompt_execution ON behavior_prompt_applications(execution_id,created_at,id)",
    )
    for statement in statements:
        conn.execute(statement)


def _table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _key(key):
    if not isinstance(key, str) or key not in DEFAULTS:
        raise settings.SettingsError('未知 Crew 提示词: ' + str(key))
    return key


def _digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _entry(conn, key, scope, identity):
    return conn.execute('SELECT * FROM behavior_prompts WHERE scope=? AND identity=? AND prompt_key=?',
                        (scope, identity, key)).fetchone() if _table(conn, 'behavior_prompts') else None


def _ownership(conn, project, session_id):
    if session_id is not None:
        scope_identity(conn, 'session', session_id)
        session = board.session(conn, session_id)
        if project is not None and project != session['project']:
            raise settings.SettingsError('AI 不属于指定工作区')
        project = session['project']
    if project is not None:
        scope_identity(conn, 'project', project)
    return project


def resolve(conn, *, project=None, session_id=None, keys=None, plugin_root=None):
    """Return exact host text with effective source/version; does not write or launch."""
    project = _ownership(conn, project, session_id)
    if keys is not None and (not isinstance(keys, (list, tuple)) or any(not isinstance(key, str) for key in keys)):
        raise settings.SettingsError('提示词列表必须为字符串数组')
    chosen = KEYS if keys is None else tuple(keys)
    if not chosen or len(set(chosen)) != len(chosen):
        raise settings.SettingsError('提示词列表必须非空且不能重复')
    prompts = []
    for key in chosen:
        _key(key)
        source, version = {'scope': 'default', 'identity': None}, 'builtin-v2'
        path = Path(plugin_root or BUILTIN_ROOT) / SPEC[key].path
        if path.is_file():
            content = path.read_text(encoding='utf-8')
            source['path'] = str(path.resolve())
        else:
            content = DEFAULTS[key]
        for scope, identity in (('global', 'global'), ('project', project), ('session', session_id)):
            if identity is None:
                continue
            row = _entry(conn, key, scope, identity)
            if row is not None and row['content'] is not None:
                content, source, version = row['content'], {'scope': scope, 'identity': identity}, row['version']
        prompts.append({'key': key, 'content': content, 'source': source, 'version': version,
                        'sha256': _digest(content)})
    return {'project': project, 'session_id': session_id, 'prompts': prompts,
            'applies': 'next_turn', 'native_private_instructions': 'not_controlled'}


def get(conn, key, *, scope='global', identity=None):
    key = _key(key)
    identity = scope_identity(conn, scope, identity)
    row = _entry(conn, key, scope, identity)
    resolved = resolve(conn, project=identity if scope == 'project' else None,
                       session_id=identity if scope == 'session' else None, keys=(key,))['prompts'][0]
    return {'key': key, 'label': LABELS[key], **SPEC[key].describe(), 'scope': scope, 'identity': identity, 'version': 0 if row is None else row['version'],
            'content': None if row is None else row['content'], 'overridden': row is not None and row['content'] is not None,
            'default': DEFAULTS[key], 'effective': resolved, 'applies': 'next_turn',
            'updated_at': None if row is None else row['updated_at']}


def _write(conn, key, content, scope, identity, expected_version, actor, action):
    key = _key(key)
    identity = scope_identity(conn, scope, identity)
    if type(expected_version) is not int or expected_version < 0:
        raise settings.SettingsError('保存提示词必须提供非负整数 expected_version')
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 300:
        raise settings.SettingsError('提示词修改必须保存有效操作者')
    if action == 'save' and (not isinstance(content, str) or not content.strip() or len(content) > SPEC[key].max_length):
        raise settings.SettingsError('提示词 %s 必须为非空文本，且不超过 %s 字符' % (LABELS[key], SPEC[key].max_length))
    if action == 'save':
        unknown = placeholders(content) - set(SPEC[key].placeholders)
        if unknown:
            allowed = '、'.join('{{%s}}' % name for name in SPEC[key].placeholders) or '无'
            raise settings.SettingsError('提示词 %s 不支持占位符 %s；可用：%s' % (
                LABELS[key], '、'.join('{{%s}}' % name for name in sorted(unknown)), allowed))
    ensure_schema(conn)
    with transaction(conn):
        previous = get(conn, key, scope=scope, identity=identity)
        if previous['version'] != expected_version:
            raise board.Conflict('提示词已变化，请保留修改并重新检查当前版本')
        stamp, version = store.now_iso(), expected_version + 1
        conn.execute('INSERT INTO behavior_prompts(scope,identity,prompt_key,content,version,updated_at) VALUES(?,?,?,?,?,?) '
                     'ON CONFLICT(scope,identity,prompt_key) DO UPDATE SET content=excluded.content,'
                     'version=excluded.version,updated_at=excluded.updated_at',
                     (scope, identity, key, content, version, stamp))
        current = get(conn, key, scope=scope, identity=identity)
        conn.execute('INSERT INTO behavior_prompt_history(scope,identity,prompt_key,version,content,effective_content,'
                     'action,actor,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                     (scope, identity, key, version, content, current['effective']['content'], action, actor, stamp))
    return current


def save(conn, key, content, *, scope='global', identity=None, expected_version=None, actor=board.HUMAN):
    return _write(conn, key, content, scope, identity, expected_version, actor, 'save')


def reset(conn, key, *, scope='global', identity=None, expected_version=None, actor=board.HUMAN):
    """Remove this override and preserve both the action and resolved text in history."""
    return _write(conn, key, None, scope, identity, expected_version, actor, 'reset')


def history(conn, key, *, scope='global', identity=None, before=None, limit=50):
    key = _key(key)
    identity = scope_identity(conn, scope, identity)
    if type(limit) is not int or not 1 <= limit <= 200 or before is not None and (type(before) is not int or before < 1):
        raise settings.SettingsError('提示词历史分页参数无效')
    if not _table(conn, 'behavior_prompt_history'):
        return {'history': [], 'next_before': None}
    rows = conn.execute('SELECT * FROM behavior_prompt_history WHERE scope=? AND identity=? AND prompt_key=?' +
                        (' AND version<?' if before is not None else '') + ' ORDER BY version DESC LIMIT ?',
                        (scope, identity, key, before, limit + 1) if before is not None else
                        (scope, identity, key, limit + 1)).fetchall()
    return {'history': [dict(row) for row in rows[:limit]],
            'next_before': rows[limit - 1]['version'] if len(rows) > limit else None}


def render(manifest):
    return ('【Crew 本次行为配置；取代先前版本的 Crew 行为配置，用户当前要求与工具身份边界仍优先】\n' +
            '\n\n'.join('[Crew ' + item['key'] + ']\n' + item['content'] for item in manifest['prompts']) +
            '\n【Crew 行为配置结束】')


def manifest_hash(manifest):
    return _digest(json.dumps(manifest['prompts'], ensure_ascii=False, sort_keys=True))


def record_applied(conn, execution_id, manifest, *, status='prepared', evidence=None, identity=None):
    """Record exactly prepared/sent text; only explicit adapter evidence confirms loading."""
    if not isinstance(execution_id, str) or not execution_id.strip() or len(execution_id) > 300:
        raise settings.SettingsError('提示词应用必须关联执行身份')
    if status not in DELIVERY_STATES or status == 'confirmed' and (not isinstance(evidence, str) or not evidence.strip()):
        raise settings.SettingsError('确认提示词加载必须提供真实载体证据')
    if evidence is not None and (not isinstance(evidence, str) or len(evidence) > 2000):
        raise settings.SettingsError('提示词加载证据必须是简短文本')
    if not isinstance(manifest, dict) or not isinstance(manifest.get('prompts'), list) or not manifest['prompts']:
        raise settings.SettingsError('提示词应用必须提供实际内容清单')
    keys = []
    for item in manifest['prompts']:
        if (not isinstance(item, dict) or not isinstance(item.get('content'), str)
                or not item['content'].strip() or item.get('sha256') != _digest(item['content'])
                or item.get('key') not in KEYS or not isinstance(item.get('source'), dict)
                or 'version' not in item):
            raise settings.SettingsError('提示词应用清单内容或摘要无效')
        keys.append(item['key'])
    if len(set(keys)) != len(keys):
        raise settings.SettingsError('提示词应用清单不能重复')
    project, session_id = manifest.get('project'), manifest.get('session_id')
    project = _ownership(conn, project, session_id)
    row = conn.execute('SELECT session_id,goal_id FROM goal_executions WHERE id=?', (execution_id,)).fetchone()
    if row is not None:
        owner = board.session(conn, row['session_id'])
        if session_id != row['session_id'] or project != owner['project']:
            raise settings.SettingsError('提示词清单不属于指定执行')
    elif execution_id != session_id and not (manifest.get('purpose') == 'summary' and execution_id == 'digest:' + str(project)):
        raise settings.SettingsError('提示词清单不属于指定总对话')
    body = json.dumps(manifest, ensure_ascii=False, sort_keys=True, allow_nan=False)
    digest = _digest(body)
    identity = str(uuid.uuid4()) if identity is None else identity
    if not isinstance(identity, str) or not identity.strip() or len(identity) > 300:
        raise settings.SettingsError('提示词应用 ID 无效')
    ensure_schema(conn)
    with transaction(conn):
        existing = conn.execute('SELECT * FROM behavior_prompt_applications WHERE id=?', (identity,)).fetchone()
        if existing is not None:
            if (existing['execution_id'], existing['manifest_hash'], existing['status'], existing['evidence']) != (execution_id, digest, status, evidence):
                raise board.Conflict('提示词应用 ID 已用于其他内容或送达状态')
            return _application(existing)
        conn.execute('INSERT INTO behavior_prompt_applications(id,execution_id,session_id,project,manifest_json,'
                     'manifest_hash,status,evidence,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                     (identity, execution_id, session_id, project, body, digest, status, evidence, store.now_iso()))
    return _application(conn.execute('SELECT * FROM behavior_prompt_applications WHERE id=?', (identity,)).fetchone())


def _application(row):
    result = dict(row)
    result['manifest'] = json.loads(result.pop('manifest_json'))
    return result


def inspect_applied(conn, execution_id):
    if not _table(conn, 'behavior_prompt_applications'):
        return {'execution_id': execution_id, 'applications': [], 'loaded': 'not_confirmed'}
    rows = conn.execute('SELECT * FROM behavior_prompt_applications WHERE execution_id=? ORDER BY created_at,rowid',
                        (execution_id,)).fetchall()
    return {'execution_id': execution_id, 'applications': [_application(row) for row in rows],
            'loaded': 'confirmed' if rows and rows[-1]['status'] == 'confirmed' else 'not_confirmed'}


def delivered(conn, identity, *, confirmed=False, evidence):
    """Transport receipts confirm delivery, never hidden native prompt contents."""
    if not isinstance(evidence, str) or not evidence.strip():
        raise settings.SettingsError('提示词送达必须提供原生回执或写入证据')
    if not _table(conn, 'behavior_prompt_applications'):
        return
    state = 'confirmed' if confirmed else 'sent'
    conn.execute("UPDATE behavior_prompt_applications SET status=?,evidence=? WHERE id=? "
                 "AND (status='prepared' OR status='sent')", (state, evidence[:2000], identity))


def prepare_input(conn, session_id, scope_id, message_id, *, plugin_root=None):
    identity = input_id(scope_id, message_id)
    if _table(conn, 'behavior_prompt_applications'):
        previous = conn.execute('SELECT * FROM behavior_prompt_applications WHERE id=?', (identity,)).fetchone()
        if previous is not None:
            return _application(previous)
    manifest = for_session(conn, session_id, plugin_root=plugin_root)
    return record_applied(conn, scope_id, manifest, identity=identity)


def input_id(scope_id, message_id):
    return 'prompt-input:' + _digest(scope_id + ':' + message_id)


def append_to_command(command, text):
    """Claude accepts one appended prompt; preserve identity/workspace notices."""
    result, parts, index = [], [], 0
    while index < len(command):
        if command[index] == '--append-system-prompt' and index + 1 < len(command):
            parts.append(command[index + 1])
            index += 2
        else:
            result.append(command[index])
            index += 1
    return result + ['--append-system-prompt', '\n\n'.join([*parts, text])]
