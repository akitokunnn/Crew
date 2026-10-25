"""Editable operations policy; reads never create HOME or run a model."""
from pathlib import Path

from .supervisor import _atomic_json, _directory, _read_json


FIELDS = {'enabled', 'repository', 'reproduce', 'verify', 'timeout_seconds', 'merge_ref'}


class OperationsSettingsError(ValueError):
    pass


def policy_path(home):
    return _directory(home) / 'operations' / 'policy.json'


def _read(home):
    try:
        value = _read_json(policy_path(home), 65536)
    except FileNotFoundError:
        return None
    if not isinstance(value, dict):
        raise OperationsSettingsError('后台修复配置必须是 JSON 对象，原文件未修改')
    return value


def enabled(home, *, policy=None):
    """Old policies retain their behavior; no policy does not start a worker."""
    value = _read(home) if policy is None else policy
    result = True if value is None else value.get('enabled', True)
    if type(result) is not bool:
        raise OperationsSettingsError('enabled 必须是 true 或 false')
    return result


def _defaults(backend_root):
    root = Path(backend_root).resolve()
    candidate = root if (root / 'plugin' / 'crew' / 'supervisor.py').is_file() else root.parent
    return {'enabled': False, 'repository': str(candidate), 'reproduce': [], 'verify': [],
            'timeout_seconds': 180, 'merge_ref': ''}


def _validate(value, *, runner=None, check_repository=False):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise OperationsSettingsError('后台配置必须且只能包含 ' + '、'.join(sorted(FIELDS)))
    if type(value['enabled']) is not bool:
        raise OperationsSettingsError('enabled 必须是 true 或 false')
    for key in ('repository', 'merge_ref'):
        if not isinstance(value[key], str):
            raise OperationsSettingsError(key + ' 必须是字符串')
    if type(value['timeout_seconds']) not in (int, float) or not 1 <= value['timeout_seconds'] <= 600:
        raise OperationsSettingsError('修复超时必须在 1 到 600 秒之间')
    for key in ('reproduce', 'verify'):
        commands = value[key]
        if (not isinstance(commands, list) or
                any(not isinstance(argv, list) or not argv or
                    any(not isinstance(arg, str) or not arg or '\0' in arg for arg in argv)
                    for argv in commands)):
            raise OperationsSettingsError(key + ' 必须是命令参数数组的列表，每条命令不能为空')
    if value['enabled']:
        from .operations import OperationsError
        from .operations_worker import _policy, validate_repository
        if not value['repository'] or not Path(value['repository']).is_absolute():
            raise OperationsSettingsError('请选择 Crew 源码 Git 仓库的绝对路径')
        try:
            _policy({**value, 'runner': runner})
            if check_repository:
                validate_repository(value['repository'])
        except OperationsError as error:
            raise OperationsSettingsError(str(error)) from error


def snapshot(home, backend_root):
    from .operations import OperationsError
    result = {'policy_path': str(policy_path(home)), 'configured': False, 'policy': _defaults(backend_root)}
    try:
        saved = _read(home)
        if saved is not None:
            result['policy'].update({key: saved[key] for key in FIELDS if key in saved})
            result['policy']['enabled'] = saved.get('enabled', True)
            _validate(result['policy'], runner=saved.get('runner'))
            result['configured'] = True
    except (OSError, ValueError, UnicodeError, OperationsError) as error:
        result['error'] = str(error)
    return result


def save(home, backend_root, value):
    from .operations_chat import configuration_write
    saved = _read(home) or {}
    _validate(value, runner=saved.get('runner'), check_repository=True)
    with configuration_write(home):
        _atomic_json(policy_path(home), {**saved, **value})
    return snapshot(home, backend_root)
