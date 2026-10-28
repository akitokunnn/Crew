"""Service lifetime routes; closing a client never invokes this feature."""
from .. import service_control


def register(routes, db):
    routes.add('GET', '/api/admin/service', lambda request: service_control.status(db.home))
    routes.add('POST', '/api/admin/service/stop', lambda request: service_control.stop(db.home))
