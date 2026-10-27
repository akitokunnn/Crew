"""Storage HTTP interface; dependencies are supplied explicitly at composition."""

from .database import text as _text, optional_text as _optional_text


FIELDS = {
    '/api/storage/preview': {'categories'},
    '/api/storage/clean': {'preview_id'},
    '/api/storage/restore': {'batch_id'},
    '/api/storage/trash/empty': {'batch_id'},
    '/api/storage/policy': {'automatic_enabled', 'retention_days', 'keep_backups', 'recycle_days'},
}


def register(routes, db, storage_cleanup):
    routes.protect_local('/api/storage')

    def mutate(request):
        path, data = request.path, request.data
        if set(data) - FIELDS[path]:
            raise ValueError('存储请求包含不支持的字段')
        if path == '/api/storage/preview':
            return storage_cleanup.preview(data.get('categories'))
        if path == '/api/storage/clean':
            return storage_cleanup.clean(_text(data, 'preview_id'))
        if path == '/api/storage/restore':
            return storage_cleanup.restore(_text(data, 'batch_id'))
        if path == '/api/storage/trash/empty':
            return storage_cleanup.empty_trash(_optional_text(data, 'batch_id'))
        return storage_cleanup.save_policy(data)

    routes.add('POST', tuple(FIELDS), mutate)

    def get_query(request):
        query = request.query
        return storage_cleanup.snapshot()

    routes.add('GET', ['/api/storage'], get_query)
