"""Request-scoped storage access, without HTTP or service-container knowledge."""
from contextlib import closing, contextmanager
from dataclasses import dataclass

from .. import board, store


def text(data, key):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError('字段必须是非空文本: ' + key)
    return value


def optional_text(data, key):
    if data.get(key) is None:
        return None
    return text(data, key)


@dataclass(frozen=True)
class Database:
    home: object
    lock: object
    now: object
    selected_project: object

    def stamp(self):
        return self.now().isoformat(timespec='milliseconds').replace('+00:00', 'Z')

    @contextmanager
    def read(self):
        with closing(store.connect(self.home)) as conn:
            yield conn

    @contextmanager
    def write(self):
        with self.lock, self.read() as conn:
            yield conn

    def project(self, conn, name, data=None):
        if data is not None and 'project' in data:
            body = text(data, 'project')
            if name is not None and body != name:
                raise board.BoardError('请求正文和地址中的项目不一致')
            name = body
        return self.selected_project(conn, name)

    def command(self, operation, *, project=True, body_project=False):
        """Keep one serialized mutation scope around validation and operation."""
        def execute(request):
            with self.write() as conn:
                selected = self.project(conn, request.project, request.data if body_project else None) if project else None
                return operation(request, conn, selected, self.stamp())
        return execute
