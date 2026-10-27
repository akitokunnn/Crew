"""Small, explicit HTTP interface shared by the built-in feature modules.

Feature modules own paths and admission policies. The transport only looks up
routes and writes the returned value; it never selects a business operation.
"""
from contextlib import nullcontext
from dataclasses import dataclass, field


class NotFound(ValueError):
    pass


@dataclass(frozen=True)
class Request:
    path: str
    query: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)
    last_event_id: int = 0
    port: int = 0

    def one(self, name, default=None):
        return self.query.get(name, [default])[0]

    @property
    def project(self):
        return self.one('project')


@dataclass(frozen=True)
class Content:
    body: bytes
    media_type: str
    attachment: str | None = None
    interactive_report: bool = False
    sandbox: bool = False


@dataclass(frozen=True)
class Download:
    path: object
    media_type: str
    attachment: str | None = None
    sandbox: bool = False


@dataclass(frozen=True)
class Events:
    journal: object
    after: int


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    handler: object
    model_work: bool = False
    local_only: bool = False
    host_only: bool = False


class Registry:
    def __init__(self, admit_work=nullcontext):
        self._routes = {}
        self._local_namespaces = set()
        self._admit_work = admit_work

    def add(self, method, paths, handler, *, model_work=False, local_only=False, host_only=False):
        if method not in ('GET', 'POST') or not callable(handler):
            raise ValueError('A feature route requires GET/POST and a callable handler')
        try:
            paths = (paths,) if isinstance(paths, str) else tuple(paths)
        except TypeError as error:
            raise ValueError('Feature paths must be a path or iterable of paths') from error
        if not paths:
            raise ValueError('A feature must register at least one path')
        pending = {}
        for path in paths:
            if not isinstance(path, str) or not path.startswith('/api/'):
                raise ValueError('Invalid feature path: ' + repr(path))
            key = (method, path)
            if key in self._routes or key in pending:
                raise ValueError('Duplicate feature route: ' + repr(key))
            pending[key] = Route(method, path, handler, model_work, local_only, host_only)
        self._routes.update(pending)

    def protect_local(self, *prefixes):
        self._local_namespaces.update(prefixes)

    def local_only(self, path):
        return (any(path == prefix or path.startswith(prefix + '/') for prefix in self._local_namespaces)
                or any(route and route.local_only for route in (self.lookup('GET', path), self.lookup('POST', path))))

    def lookup(self, method, path):
        return self._routes.get((method, path))

    def routes(self, method=None):
        return tuple(route for route in self._routes.values() if method is None or route.method == method)

    def dispatch(self, method, request):
        route = self.lookup(method, request.path)
        if route is None:
            raise NotFound('接口不存在')
        if method == 'POST' and not isinstance(request.data, dict):
            raise ValueError('JSON 请求必须是对象')
        with self._admit_work() if route.model_work else nullcontext():
            return route.handler(request)
