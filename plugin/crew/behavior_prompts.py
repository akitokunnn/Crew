"""Editable Crew instructions and truthful per-execution delivery manifests.

These are host-injected instructions. They do not expose or replace a carrier's
private system prompt, skills, MCP discovery or native project configuration.
"""
import hashlib
import json
import uuid

from . import board, settings, store


DEFAULTS = {
    'common': ('遵循用户当前目标、明确约束和有效决定。普通闲聊不等于执行授权。'
               '已授权且有具体下一步时持续推进；需要真实用户资料、手动停止、故障受阻或达到完成标准时如实记录状态。'
               '没有可执行工作时不要空转、自我汇报或反复发送“继续”。'
               '工程选择自行处理，重要取舍按本任务模式执行；不扩大本任务授权。'
               '以实际证据、实验和相关验证判断结果，保留真实错误；不要静默换路径、重做已成功步骤或编造验收。'
               '有新结论、重要取舍或可体验成果时更新记录；普通沟通直接回答。'),
    'leader': ('你负责理解需求、维护同一个任务的标题、目标、条件、完成标准、进度和下一步。'
               '优先自己推进，仅在存在独立工作时创建成员，交给成员相关要求、边界、已有结论和验收依据。'
               '维护任务关联和执行归属；换模型、载体或恢复不创建重复任务。'
               '用户把关模式在原任务对话讨论新框架、审美、交互体验和重要取舍，其他独立工作继续。'
               '全自动模式在已授权目标及约束内选择路线和实验，缺失凭证和必须由人完成的操作仍创建 TODO。'
               '协调共享文件、接口和整体体验，保留用户及其他任务修改；最终交付核对实际成果和完成标准。'),
    'member': ('只处理交给你的独立工作和明确边界。使用相关需求、有效决定、必要上下文及已有证据；'
               '需要更多资料时按需检索原始记录，不复制所有任务历史。'
               '报告实际修改、验证、结果和真实阻碍；已完成的成功操作不要重复执行。'
               '用户直接补充中影响整体任务的要求保留原话及来源，同步给负责人；普通问答直接回答。'
               '保留原生子代理能力，说明执行、停止与结果归属，避免层层重复转述。'),
    'assistant': ('你是用户的个人助手，帮助查询和整理 Crew 的工作区、任务、TODO、灵感与成果。'
                  '按需读取信息，不把全部项目历史常驻装载。'
                  '“记下来”先保存用户原话，不强制追问；“开始聊”进入相关任务或创建讨论任务。'
                  '收到明确指令后通过 Crew 同一业务入口创建任务、联系负责人、查进度、停止指定任务或修改明确范围的设置，保留操作来源。'
                  '单次要求留在任务里，用户明确说“以后都这样”才修改长期设置并说明作用范围。'
                  '可整理、归档或移入垃圾箱，不能永久清空；普通聊天不擅自执行项目工作。'),
    'report': ('每个实际执行并交付成果的任务维护一份持续更新的报告。'
               '第一段直接说明结果或当前结论，使用短段落；适合比较的属性用表格，分支关系用图，趋势使用真实数据。'
               '只更新有新结论、实验、重要取舍或可体验成果的相关节，保留用户编辑并使用当前文档版本保存；冲突保留双方内容。'
               '尚待查证部分明确说明用途和状态；外部事实须实际打开来源并附链接与截至日期。'
               '阶段版本关联可取得的实际文件或内容及来源任务，应用版本与任务成果版本分别表达。'
               '按完成标准交付，报告完成或到达汇报时间不代表任务已经完成；不得把模拟验证声称为真实模型或整夜运行验收。'),
    'context': ('本轮只装载原始要求、当前需求版本、有效决定、当前计划、相关文件、实验结论、验证依据和必要近期交流。'
                '保留来源和适用版本；旧结论被纠正后标为失效，不继续当作现行条件。'
                '成员仅收到与本次工作相关的内容，必要时可检索原始记录。'
                '压缩和换载体交接须保留关键决定、未完成工作、真实错误、已有成果和来源，不能只留“继续”。'
                '垃圾箱内容不进入常规活动上下文；用户、成员与私聊消息保留真实来源，不循环转发或无限唤醒。'),
}
KEYS = tuple(DEFAULTS)
DELIVERY_STATES = ('prepared', 'sent', 'confirmed')


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
        settings.scope_identity(conn, 'session', session_id)
        session = board.session(conn, session_id)
        if project is not None and project != session['project']:
            raise settings.SettingsError('AI 不属于指定工作区')
        project = session['project']
    if project is not None:
        settings.scope_identity(conn, 'project', project)
    return project


def resolve(conn, *, project=None, session_id=None, keys=None):
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
        content, source, version = DEFAULTS[key], {'scope': 'default', 'identity': None}, 'builtin-v1'
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
    identity = settings.scope_identity(conn, scope, identity)
    row = _entry(conn, key, scope, identity)
    resolved = resolve(conn, project=identity if scope == 'project' else None,
                       session_id=identity if scope == 'session' else None, keys=(key,))['prompts'][0]
    return {'key': key, 'scope': scope, 'identity': identity, 'version': 0 if row is None else row['version'],
            'content': None if row is None else row['content'], 'overridden': row is not None and row['content'] is not None,
            'default': DEFAULTS[key], 'effective': resolved, 'applies': 'next_turn',
            'updated_at': None if row is None else row['updated_at']}


def _write(conn, key, content, scope, identity, expected_version, actor, action):
    key = _key(key)
    identity = settings.scope_identity(conn, scope, identity)
    if type(expected_version) is not int or expected_version < 0:
        raise settings.SettingsError('保存提示词必须提供非负整数 expected_version')
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 300:
        raise settings.SettingsError('提示词修改必须保存有效操作者')
    if action == 'save' and (not isinstance(content, str) or not content.strip() or len(content) > 100000):
        raise settings.SettingsError('Crew 提示词必须为非空文本，且不超过 100000 字符')
    ensure_schema(conn)
    with settings.scoped_transaction(conn):
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
    identity = settings.scope_identity(conn, scope, identity)
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
    return '\n\n'.join('[Crew ' + item['key'] + ']\n' + item['content'] for item in manifest['prompts'])


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
    body = json.dumps(manifest, ensure_ascii=False, sort_keys=True, allow_nan=False)
    digest = _digest(body)
    identity = str(uuid.uuid4()) if identity is None else identity
    if not isinstance(identity, str) or not identity.strip() or len(identity) > 300:
        raise settings.SettingsError('提示词应用 ID 无效')
    ensure_schema(conn)
    with settings.scoped_transaction(conn):
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
    rows = conn.execute('SELECT * FROM behavior_prompt_applications WHERE execution_id=? ORDER BY created_at,id',
                        (execution_id,)).fetchall()
    return {'execution_id': execution_id, 'applications': [_application(row) for row in rows],
            'loaded': 'confirmed' if any(row['status'] == 'confirmed' for row in rows) else 'not_confirmed'}
