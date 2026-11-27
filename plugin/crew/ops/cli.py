"""The ``crew`` command line, generated from the registry (design §6).

Every business command is an operation the local user may call; the CLI sends
it to the running Crew backend through ``POST /api/op`` as ``human``. It never
reads a session identity from the environment: AI sessions use MCP.
Host commands (install, start, web, doctor, usage, ...) stay hand-written in
``crew.cli`` and are listed in :data:`HOST_COMMANDS`.
"""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sys
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .. import board, store
from . import registry

HOST_COMMANDS = ('web', 'start', 'install', 'autostart', 'menu', 'doctor', 'usage')


def command_path(op):
    return tuple(op.cli.path) if op.cli is not None else tuple(op.name.split('.'))


def operations():
    return {command_path(op): op for op in registry.operations().values() if 'human' in op.scopes}


def _flag(name):
    return '--' + name.replace('_', '-')


def _add_arguments(parser, op):
    schema = op.params
    properties = schema.get('properties', {})
    required = set(schema.get('required', ()))
    for name, spec in properties.items():
        kinds = spec['type'] if isinstance(spec['type'], list) else [spec['type']]
        help_text = spec.get('description')
        if 'boolean' in kinds:
            parser.add_argument(_flag(name), dest='arg_' + name, action=argparse.BooleanOptionalAction, help=help_text)
        elif 'array' in kinds:
            parser.add_argument(_flag(name), dest='arg_' + name, action='append', help=help_text,
                                required=name in required)
        elif 'object' in kinds:
            parser.add_argument(_flag(name) + '-json', dest='arg_' + name, type=json.loads, help=help_text,
                                required=name in required)
        else:
            parser.add_argument(_flag(name), dest='arg_' + name, type=int if 'integer' in kinds else
                                float if 'number' in kinds else str, help=help_text, required=name in required)
    if schema.get('additionalProperties') is True:
        parser.add_argument('--args-json', dest='args_json', type=json.loads,
                            help='整个参数对象（JSON）；与界面调用的字段相同')


def parser():
    root = argparse.ArgumentParser(prog='crew', description='Crew 命令行：以用户身份调用 Crew 操作，需要 Crew 后台在运行。')
    root.add_argument('--home', type=Path, default=Path(os.environ['CREW_HOME']) if os.environ.get('CREW_HOME') else store.default_home())
    root.add_argument('--project', help='工作区名；不填按当前目录推断')
    root.add_argument('--request-id', help='写操作的稳定编号；重试同一操作时传相同值，不填自动生成')
    root.add_argument('--json', action='store_true', help='输出原始 JSON')
    nodes = {(): root.add_subparsers(dest='cmd0', metavar='<领域>', required=True)}
    parsers = {}
    for path, op in sorted(operations().items()):
        for depth in range(1, len(path)):
            prefix = path[:depth]
            if prefix not in nodes:
                group = nodes[prefix[:-1]].add_parser(prefix[-1], help='%s 相关操作' % '.'.join(prefix))
                nodes[prefix] = group.add_subparsers(dest='cmd%d' % depth, metavar='<操作>', required=True)
        leaf = nodes[path[:-1]].add_parser(path[-1], help=op.summary, description=op.description or op.summary)
        leaf.set_defaults(op=op.name)
        _add_arguments(leaf, op)
        parsers[path] = leaf
    return root


def _arguments(namespace):
    args = dict(getattr(namespace, 'args_json', None) or {})
    for key, value in vars(namespace).items():
        if key.startswith('arg_') and value is not None:
            args[key[4:]] = value
    return args


def _project(home, explicit, cwd):
    if explicit is not None:
        return explicit
    db = store.db_path(home)
    if not db.exists():
        return None
    from .. import guard
    with closing(guard.readonly(home)) as conn:
        found = board.project_for_path(conn, cwd)
    return found['name'] if found else None


def _post(home, envelope):
    address = Path(home) / 'web.json'
    if not address.exists():
        raise board.BoardError('Crew 后台未运行：先 crew start 或打开 Crew')
    url = json.loads(address.read_text(encoding='utf-8'))['url'] + '/api/op'
    request = Request(url, json.dumps(envelope, ensure_ascii=False).encode('utf-8'),
                      {'X-Crew': '1', 'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=120) as response:
            body = response.read()
            if 'json' in response.headers.get('Content-Type', ''):
                return json.loads(body)
            return body
    except HTTPError as exc:
        with exc:
            payload = json.load(exc)
        raise (board.Conflict if exc.code == 409 else board.BoardError)(payload.get('error') or str(payload)) from exc
    except URLError as exc:
        raise board.BoardError('连不上 Crew 后台：%s' % exc.reason) from exc


def main(argv, cwd=None):
    namespace = parser().parse_args(argv)
    op = registry.get(namespace.op)
    envelope = {'op': op.name, 'args': _arguments(namespace)}
    project = _project(namespace.home, namespace.project, Path.cwd() if cwd is None else Path(cwd))
    if project is not None:
        envelope['project'] = project
    if op.access != 'read':
        envelope['request_id'] = namespace.request_id or 'cli-' + uuid.uuid4().hex
    result = _post(namespace.home, envelope)
    if isinstance(result, bytes):
        sys.stdout.buffer.write(result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=None if namespace.json else 2))
    return 0
