"""Settings HTTP interface; dependencies are supplied explicitly at composition."""
from .. import board, goals, member_defaults, operations_chat, operations_settings, settings
from .database import text as _text


def register(routes, db, connection_plan, connections, health_snapshot, plugin_root, runtime_catalog, set_away, settings_state, prepare_update, cancel_update, prepare_connection):

    def post_operations_send(request):
        data = request.data
        return operations_chat.send(db.home, plugin_root, data)

    routes.add('POST', '/api/admin/chat/send', post_operations_send)

    def post_operations_interrupt(request):
        return operations_chat.interrupt(db.home)

    routes.add('POST', '/api/admin/chat/interrupt', post_operations_interrupt)

    def post_operations_config(request):
        data = request.data
        return operations_settings.save(db.home, plugin_root, data)

    routes.add('POST', '/api/admin/config', post_operations_config)

    def post_connection_configure(request):
        data = request.data
        return connections.configure(data)

    routes.add('POST', '/api/connections/configure', post_connection_configure, local_only=True)

    def post_connection_action(request):
        data = request.data
        return connections.action(data)

    routes.add('POST', '/api/connections/action', post_connection_action, local_only=True)

    def post_connection_preferences(request):
        data = request.data
        return connections.save_preferences(data)

    routes.add('POST', '/api/connections/preferences', post_connection_preferences, local_only=True)

    def post_member_defaults(request):
        data, project_name = request.data, request.project
        if set(data) != {'leader_session_id', 'selection'}:
            raise ValueError('组员默认设置必须包含 leader_session_id 和 selection')
        with db.write() as conn:
            project = db.selected_project(conn, project_name)
            sid = _text(data, 'leader_session_id')
            if board.session(conn, sid)['project'] != project:
                raise board.BoardError('leader 不属于当前工作区')
            return member_defaults.save(conn, sid, data['selection'], now=db.now().isoformat(timespec='milliseconds').replace('+00:00', 'Z'))

    routes.add('POST', '/api/member-defaults', post_member_defaults)

    def post_save_settings(request):
        data = request.data
        if not data or not set(data) <= {'ai', 'execution'}:
            raise ValueError('设置请求只能包含 ai 和 execution 配置')
        with db.write() as conn, goals.transaction(conn):
            if 'ai' in data:
                settings.save(conn, data['ai'])
            if 'execution' in data:
                settings.save_execution(conn, data['execution'])
        return settings_state()

    routes.add('POST', '/api/settings', post_save_settings)

    def post_away(request, conn, project, now):
        data = request.data
        if type(data.get('away')) is not bool:
            raise ValueError('离开状态必须是 true 或 false')
        return set_away(data['away'])

    routes.add('POST', '/api/notifications/away', db.command(post_away, project=False))

    def get_connection_plan(request):
        query = request.query
        return connection_plan()

    routes.add('GET', ['/api/admin/connection/plan'], get_connection_plan)

    def get_operations_snapshot(request):
        query = request.query
        return operations_chat.snapshot(db.home)

    routes.add('GET', ['/api/admin/chat'], get_operations_snapshot)

    def get_operations_settings(request):
        query = request.query
        return operations_settings.snapshot(db.home, plugin_root)

    routes.add('GET', ['/api/admin/config'], get_operations_settings)

    def get_health(request):
        query = request.query
        return health_snapshot(request.port)

    routes.add('GET', ['/api/admin/health'], get_health)

    def get_runtimes(request):
        query = request.query
        catalog = runtime_catalog.refresh(force=True) if query.get('refresh') == ['1'] else runtime_catalog.refresh()
        return {'runtimes': catalog}

    routes.add('GET', ['/api/runtimes'], get_runtimes)

    def get_connections(request):
        query = request.query
        return connections.snapshot()

    routes.add('GET', ['/api/connections'], get_connections, local_only=True)

    def get_query(request):
        query = request.query
        return settings_state()

    routes.add('GET', ['/api/settings'], get_query)

    routes.add('POST', '/api/admin/update/prepare', lambda request: prepare_update(request.data))
    routes.add('POST', '/api/admin/update/cancel', lambda request: cancel_update(request.data))
    routes.add('POST', '/api/admin/connection/prepare', lambda request: prepare_connection(request.data))
