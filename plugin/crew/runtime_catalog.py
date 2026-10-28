"""Native model discovery and configuration evidence; never starts an AI turn."""
from contextlib import closing
import copy
import json
from pathlib import Path
import shutil
import sqlite3
import threading
import time
import tomllib

from . import codex_client, guard, settings, store


def _source(kind, path=None, **fields):
    return {'kind': kind, **({'path': str(path)} if path is not None else {}), **fields}


def _native_configuration(runtime, root):
    selections, capabilities, errors = [], [], []
    if root is None:
        return selections, capabilities, ['尚未指定原生配置目录；请在设置中选择载体和模型']
    root = Path(root)
    files = [root / 'config.toml'] if runtime == 'codex' else [
        root / 'settings.json', root / 'settings.local.json', root.parent / '.claude.json']
    for path in files:
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError('配置文件超过 16 MiB 读取上限')
            body = path.read_text(encoding='utf-8-sig')
            value = tomllib.loads(body) if path.suffix == '.toml' else json.loads(body)
            if not isinstance(value, dict):
                raise ValueError('原生配置必须为对象')
            source = _source('native_config', path)
            model = value.get('model')
            effort = value.get('model_reasoning_effort' if runtime == 'codex' else 'effortLevel')
            if model is not None or effort is not None:
                if any(item is not None and (not isinstance(item, str) or not item.strip()) for item in (model, effort)):
                    raise ValueError('模型及effort必须为有效文本或null')
                selections.append({'runtime': runtime, 'model': model, 'effort': effort, 'source': source})
            servers = value.get('mcp_servers' if runtime == 'codex' else 'mcpServers', {})
            if not isinstance(servers, dict):
                raise ValueError('MCP 配置必须为对象')
            capabilities.extend({'kind': 'mcp', 'name': name, 'status': 'configured',
                'enabled': not isinstance(config, dict) or config.get('enabled', True) is not False,
                'source': source} for name, config in servers.items())
            plugins = value.get('enabledPlugins', {})
            if isinstance(plugins, dict):
                capabilities.extend({'kind': 'plugin', 'name': name, 'enabled': enabled is True,
                    'status': 'configured', 'source': source} for name, enabled in plugins.items())
        except (OSError, UnicodeError, ValueError, TypeError) as error:
            errors.append(str(path) + ': ' + type(error).__name__ + ': ' + str(error))
    try:
        skills = root / 'skills'
        if skills.is_dir():
            for directory in sorted(skills.iterdir()):
                entry = directory / 'SKILL.md'
                if not directory.name.startswith('.') and entry.is_file():
                    capabilities.append({'kind': 'skill', 'name': directory.name, 'status': 'discovered',
                        'source': _source('native_skill', entry)})
        instructions = root / ('AGENTS.md' if runtime == 'codex' else 'CLAUDE.md')
        if instructions.is_file():
            capabilities.append({'kind': 'project_instructions', 'name': instructions.name,
                'status': 'discovered', 'source': _source('native_instructions', instructions)})
    except OSError as error:
        errors.append(str(root) + ': ' + type(error).__name__ + ': ' + str(error))
    return selections, capabilities, errors


class Catalog:
    def __init__(self, client_factory=codex_client.Client, which=shutil.which, *, home=None,
                 claude_config_root=None, codex_config_root=None, configured_provider=None, loaded_provider=None):
        self.client_factory, self.which = client_factory, which
        self.home = None if home is None else Path(home)
        self.roots = {'claude': claude_config_root, 'codex': codex_config_root}
        self.configured_provider, self.loaded_provider = configured_provider, loaded_provider
        self.lock = threading.RLock()
        self.updated_at = None
        self.codex = {'models': [], 'efforts': [], 'discovery': {'status': 'not_requested',
            'source': 'codex:model/list', 'error': '尚未请求原生模型发现；可在设置中重新检测'}}

    def _crew_choices(self):
        if self.configured_provider is not None:
            choices = self.configured_provider()
            if not isinstance(choices, list):
                raise ValueError('配置提供者必须返回数组')
            return choices
        if self.home is None or not store.db_path(self.home).exists():
            return []
        result = []
        with closing(guard.readonly(self.home)) as conn:
            for purpose, choice in settings.get(conn).items():
                if choice and choice.get('runtime'):
                    result.append({**choice, 'source': _source('crew_settings', scope='global', purpose=purpose)})
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings_scopes'").fetchone():
                for row in conn.execute('SELECT scope,identity,ai_json FROM settings_scopes'):
                    for purpose, choice in json.loads(row['ai_json']).items():
                        if choice and choice.get('runtime'):
                            result.append({**choice, 'source': _source('crew_settings', scope=row['scope'],
                                identity=row['identity'], purpose=purpose)})
            for row in conn.execute("SELECT o.*,COALESCE(r.runtime,'claude') runtime FROM session_options o "
                                    "LEFT JOIN session_runtimes r ON r.session_id=o.session_id"):
                result.append({'runtime': row['runtime'], 'model': row['model'], 'effort': row['effort'],
                    'source': _source('crew_session', session_id=row['session_id'])})
        return result

    def _observations(self):
        # External query ports may acquire the execution registry lock. Never
        # call them while holding the catalog lock (runtime switching uses both).
        try:
            choices, choice_errors = self._crew_choices(), []
        except (OSError, ValueError, TypeError, sqlite3.Error) as error:
            choices, choice_errors = [], [type(error).__name__ + ': ' + str(error)]
        try:
            loaded = [] if self.loaded_provider is None else self.loaded_provider()
            if not isinstance(loaded, list):
                raise ValueError('原生执行观测必须为数组')
            loaded_errors = []
        except (OSError, ValueError, TypeError) as error:
            loaded, loaded_errors = [], [type(error).__name__ + ': ' + str(error)]
        return choices, loaded, choice_errors + loaded_errors

    def snapshot(self):
        choices, loaded, external_errors = self._observations()
        with self.lock:
            result = []
            for runtime, label in (('claude', 'Claude Code'), ('codex', 'Codex')):
                try:
                    configured, capabilities, errors = _native_configuration(runtime, self.roots[runtime])
                except OSError as error:
                    configured, capabilities, errors = [], [], [type(error).__name__ + ': ' + str(error)]
                configured += [item for item in choices if item.get('runtime') == runtime]
                observed = [item for item in loaded if item.get('runtime') == runtime]
                entries = copy.deepcopy(self.codex['models']) if runtime == 'codex' else []
                for selection in configured:
                    identity = selection.get('model')
                    if not identity:
                        continue
                    entry = next((item for item in entries if item['id'] == identity), None)
                    if entry is None:
                        entry = {'id': identity, 'name': identity, 'status': 'configured', 'sources': [],
                            'capabilities_status': 'not_confirmed'}
                        entries.append(entry)
                    entry.setdefault('sources', []).append(selection.get('source', _source('user_selection')))
                for observation in observed:
                    identity = observation.get('model')
                    if identity and not any(item['id'] == identity for item in entries):
                        entries.append({'id': identity, 'name': identity, 'status': 'observed',
                            'sources': [observation.get('source', _source('native_execution'))],
                            'capabilities_status': 'not_confirmed'})
                discovery = copy.deepcopy(self.codex['discovery']) if runtime == 'codex' else {
                    'status': 'unsupported', 'source': 'claude',
                    'error': '当前Claude适配未提供可枚举模型协议；使用原生配置或用户指定模型，能力待实际执行确认'}
                errors += external_errors
                available = bool(self.which(runtime))
                efforts = list(self.codex['efforts']) if runtime == 'codex' and discovery['status'] == 'discovered' else []
                efforts += [item['effort'] for item in configured if item.get('effort') and item['effort'] not in efforts]
                efforts += [item['effort'] for item in observed if item.get('effort') and item['effort'] not in efforts]
                result.append({'id': runtime, 'name': label, 'available': available,
                    'error': None if available else '尚未安装 ' + label + ' CLI', 'models': entries, 'efforts': efforts,
                    'configured': configured, 'discovery': discovery, 'capabilities': capabilities,
                    'loaded': {'status': 'confirmed' if observed else 'not_confirmed', 'executions': observed,
                        'note': '只确认载体实际回报的字段；未回报的Skill/MCP加载情况保持未确认'},
                    'configuration_errors': errors, 'configuration_entry': {'action': 'settings', 'label': '全局设置 → AI 配置',
                        'path': str(self.roots[runtime]) if self.roots[runtime] is not None else None}})
            return result

    def refresh(self, force=False):
        with self.lock:
            fresh = not force and self.updated_at is not None and time.monotonic() - self.updated_at < 300
        if fresh:
            return self.snapshot()
        with self.lock:
            if not self.which('codex'):
                self.codex.update(models=[], efforts=[], discovery={'status': 'unavailable',
                    'source': 'codex:model/list', 'error': '尚未安装Codex CLI'})
            else:
                try:
                    with self.client_factory(timeout=15) as client:
                        entries, cursor, seen_cursors = [], None, set()
                        while True:
                            page = client.list_models(**({'cursor': cursor} if cursor else {}))
                            for item in page['data']:
                                efforts = [choice['reasoningEffort'] for choice in item.get('supportedReasoningEfforts', [])]
                                identity = item.get('model') or item['id']
                                entries.append({'id': identity, 'name': item.get('displayName') or item['id'],
                                    'efforts': efforts, 'default_effort': item.get('defaultReasoningEffort'),
                                    'context_window': item.get('contextWindow'), 'status': 'discovered',
                                    'sources': [_source('native_discovery', protocol='codex:model/list')],
                                    'capabilities_status': 'discovered'})
                            cursor = page.get('nextCursor')
                            if not cursor:
                                break
                            if cursor in seen_cursors:
                                raise ValueError('Codex model/list返回了重复分页标识')
                            seen_cursors.add(cursor)
                    efforts = list(dict.fromkeys(effort for item in entries for effort in item['efforts']))
                    self.codex.update(models=entries, efforts=efforts,
                        discovery={'status': 'discovered', 'source': 'codex:model/list', 'error': None})
                except (OSError, ValueError, KeyError, TypeError, codex_client.CodexError) as error:
                    self.codex['discovery'] = {'status': 'failed', 'source': 'codex:model/list',
                        'error': 'Codex模型清单读取失败：' + type(error).__name__ + ': ' + str(error)}
            self.updated_at = time.monotonic()
        return self.snapshot()
