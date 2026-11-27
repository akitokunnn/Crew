"""Shared helpers for registering operations."""
from .. import legacy
from ..registry import Operation, register

H = frozenset(('human',))
HD = frozenset(('human', 'device_desktop'))
HBD = frozenset(('human', 'device_browser', 'device_desktop'))


def route(name, summary, method, path, function, *, access, lock='none', project='none', admission='none',
          risk='normal', scopes=HBD, when=(), forward_request_id=False, description='', params=None,
          target='any', emits=None):
    """Register an operation handled by an existing ``web_api`` route function.

    ``method``/``path``/``when`` are what that function was written for (it
    branches on them); the old URL itself is not served. A GET function keeps
    its raw query and response, so ``GET /api/op/<name>`` serves it as before.
    """
    handler = legacy.get(function, path, tuple(when)) if method == 'GET' else legacy.post(function, path, tuple(when))
    fields = {} if params is None else {'params': params}
    return register(Operation(
        name=name, summary=summary, description=description, access=access, risk=risk, scopes=frozenset(scopes),
        target=target, lock=lock, project=project, admission=admission, handler=handler,
        query_get=method == 'GET', forward_request_id=forward_request_id,
        emits=(name,) if emits is None and access != 'read' else tuple(emits or ()), **fields))


def act(action):
    return (('action', action),)
