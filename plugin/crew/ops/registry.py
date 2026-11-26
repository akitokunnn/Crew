"""Operation definitions and the process-wide registry (design §2).

Every business action is registered exactly once. Registration enforces the
field rules that the coverage tests rely on; a malformed entry fails at import.
"""
from dataclasses import dataclass, field
import re
from typing import Callable

from . import schema as schema_check

ROLES = ('human', 'device_browser', 'device_desktop', 'assistant', 'leader', 'execution', 'git_admin')
AI_ROLES = frozenset(('assistant', 'leader', 'execution', 'git_admin'))
HUMAN_ROLES = frozenset(('human', 'device_browser', 'device_desktop'))
ACCESS = ('read', 'write', 'external', 'host')
RISK = ('normal', 'confirm', 'high')
TARGET = ('none', 'self', 'own_group', 'own_project', 'own_goal', 'any')
# none: the handler manages its own locking (W2 segment A, not "lock-free");
# read: the executor opens a read-only connection; outer: write lock + connection
# (W2 segment B); project: outer + resolved project (W2 segment C).
LOCK = ('none', 'read', 'outer', 'project')
PROJECT = ('none', 'selected', 'chat', 'body')
ADMISSION = ('none', 'model_work')
NAME = re.compile(r'^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$')
OPEN = {'type': 'object', 'additionalProperties': True}


@dataclass(frozen=True)
class Binding:
    """A legacy HTTP address: (method, path, discriminator values merged into the body).

    ``handler`` runs the old route function with the old body when the
    operation's own handler takes the unified arguments instead.
    """
    method: str
    path: str
    when: tuple = ()
    handler: Callable = field(default=None, compare=False)

    @property
    def key(self):
        return (self.method, self.path, self.when)


@dataclass(frozen=True)
class CliSpec:
    path: tuple
    positional: tuple = ()


@dataclass(frozen=True)
class Hints:
    read_only: bool
    destructive: bool
    idempotent: bool
    open_world: bool


@dataclass(frozen=True)
class Operation:
    name: str
    summary: str
    access: str
    risk: str
    scopes: frozenset
    target: str
    lock: str
    project: str
    admission: str
    handler: Callable
    params: dict = field(default_factory=lambda: dict(OPEN))
    # Arguments for AI callers (MCP inputSchema); None means the same as params.
    ai_params: dict = None
    description: str = ''
    result: dict = None
    http: tuple = ()
    ipc: tuple = ()
    cli: CliSpec = None
    core_for: frozenset = frozenset()
    emits: tuple = ()
    # The legacy body carries the envelope request_id (personal notes and tags).
    forward_request_id: bool = False

    @property
    def hints(self):
        return Hints(read_only=self.access == 'read', destructive=self.risk == 'high',
                     idempotent=self.access == 'read', open_world=self.access in ('external', 'host'))

    def schema_for(self, caller):
        return self.ai_params if caller.is_ai and self.ai_params is not None else self.params

    @property
    def tool_name(self):
        return self.name.replace('.', '_')


_OPS = {}
_BINDINGS = {}


def register(op):
    problems = []
    if not NAME.match(op.name):
        problems.append('name must be lowercase dotted')
    if op.name in _OPS:
        problems.append('duplicate operation')
    for value, allowed, label in ((op.access, ACCESS, 'access'), (op.risk, RISK, 'risk'), (op.target, TARGET, 'target'),
                                  (op.lock, LOCK, 'lock'), (op.project, PROJECT, 'project'),
                                  (op.admission, ADMISSION, 'admission')):
        if value not in allowed:
            problems.append('%s=%r' % (label, value))
    if not op.scopes or not set(op.scopes) <= set(ROLES):
        problems.append('scopes must be a nonempty subset of %s' % (ROLES,))
    if not set(op.core_for) <= set(op.scopes):
        problems.append('core_for must be within scopes')
    if (op.lock == 'project') != (op.project != 'none'):
        problems.append('project resolution belongs exactly to lock=project')
    if op.access == 'read' and op.lock not in ('none', 'read'):
        problems.append('read operations cannot take the write lock')
    if not op.summary.strip():
        problems.append('summary is required')
    for label, value in (('params', op.params), ('ai_params', op.ai_params)):
        try:
            if value is not None:
                schema_check.check(value, label)
        except ValueError as exc:
            problems.append(str(exc))
    if set(op.scopes) & AI_ROLES:
        schema = op.ai_params or op.params
        if schema.get('additionalProperties') is True and not schema.get('properties'):
            problems.append('operations open to AI roles need declared arguments')
    for binding in op.http:
        if binding.method not in ('GET', 'POST'):
            problems.append('binding method %r' % binding.method)
        if binding.key in _BINDINGS:
            problems.append('binding %r already belongs to %s' % (binding.key, _BINDINGS[binding.key].name))
    if problems:
        raise ValueError('Invalid operation %s: %s' % (op.name, '; '.join(problems)))
    _OPS[op.name] = op
    for binding in op.http:
        _BINDINGS[binding.key] = op
    return op


def amend(name, **changes):
    """Replace a registered operation with changed fields, re-running every check."""
    from dataclasses import replace
    op = _OPS.pop(name)
    for binding in op.http:
        _BINDINGS.pop(binding.key)
    try:
        return register(replace(op, **changes))
    except ValueError:
        register(op)
        raise


def get(name):
    return _OPS.get(name)


def operations():
    from . import domains  # noqa: F401  (registers every domain on first use)
    return dict(_OPS)


def bindings():
    operations()
    return dict(_BINDINGS)


def resolve(method, path, data):
    """Find the operation bound to a legacy address; discriminators must all match."""
    operations()
    best = None
    for (bound_method, bound_path, when), op in _BINDINGS.items():
        if bound_method == method and bound_path == path and all(
                isinstance(data, dict) and data.get(key) == value for key, value in when):
            if best is None or len(when) > len(best[1].when):
                best = (op, next(b for b in op.http if b.key == (bound_method, bound_path, when)))
    return best


def paths(method):
    return {path for (bound_method, path, _), _op in bindings().items() if bound_method == method}
