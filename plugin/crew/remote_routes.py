"""Which addresses authenticated remote devices may reach; operations check the device scope."""
from urllib.parse import unquote, urlsplit

CAPABILITIES = {key: True for key in ('chat', 'tasks', 'decisions', 'todos', 'notes', 'reports', 'files')}
DESKTOP_CAPABILITIES = {**CAPABILITIES, 'settings': True, 'workspaces': True, 'storage': True}
# Access probing and event streams are transport routes. Desktop also probes health.
_SHARED_GET = frozenset(('/api/access/context', '/api/chat/events', '/api/events', '/api/notifications/events'))
TRANSPORT_GET = {'browser': _SHARED_GET, 'desktop': _SHARED_GET | {'/api/admin/health'}}
OP_PATH = '/api/op'


def permitted(method, path, static_paths, kind):
    """Which addresses a remote device may reach; what it may do there is the operation's device scope."""
    if path == OP_PATH:
        return method == 'POST'
    if path.startswith(OP_PATH + '/'):
        return method in ('GET', 'POST')
    return method == 'GET' and (path in static_paths or path in TRANSPORT_GET[kind])


def safe_next(value):
    """Accept only a same-origin absolute-path reference, retaining its fragment."""
    if not isinstance(value, str) or len(value) > 4096:
        return '/'
    try:
        decoded = unquote(value)
        parsed = urlsplit(value)
    except ValueError:
        return '/'
    if (not value.startswith('/') or value.startswith('//') or decoded.startswith('//')
            or '\\' in value or '\\' in decoded or parsed.scheme or parsed.netloc
            or any(ord(char) < 32 or ord(char) == 127 for char in decoded)
            or parsed.path in ('/remote/login', '/remote/logout')):
        return '/'
    return value
