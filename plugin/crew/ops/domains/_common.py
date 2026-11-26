"""Shared helpers for registering operations."""
from .. import legacy
from ..registry import Binding, Operation, register

H = frozenset(('human',))
HD = frozenset(('human', 'device_desktop'))
HBD = frozenset(('human', 'device_browser', 'device_desktop'))


def route(name, summary, method, path, function, *, access, lock='none', project='none', admission='none',
          risk='normal', scopes=HBD, when=(), also=(), forward_request_id=False, description='', params=None,
          target='any', emits=None):
    """Register an operation served by an existing ``web_api`` route function.

    ``when`` holds discriminator values the old URL needs in its body; ``also``
    lists further old addresses that reach the same operation.
    """
    handler = legacy.get(function) if method == 'GET' else legacy.post(function)
    fields = {} if params is None else {'params': params}
    return register(Operation(
        name=name, summary=summary, description=description, access=access, risk=risk, scopes=frozenset(scopes),
        target=target, lock=lock, project=project, admission=admission, handler=handler,
        http=(Binding(method, path, tuple(when)),) + tuple(also), forward_request_id=forward_request_id,
        emits=(name,) if emits is None and access != 'read' else tuple(emits or ()), **fields))


def act(action):
    return (('action', action),)
