"""Settings and host administration HTTP routes."""
import io
import os
import socket
from contextlib import closing
from pathlib import Path

from .. import board, claude_budget, goals, onboarding, runtime_catalog, settings, store, usage_cli
from .. import member_defaults, operations_chat, operations_settings
from .. import event_sounds
from . import behavior_prompts as behavior_prompts_api
from .common import _text


def post_remote_access(service, path, data, project_name):
    return service.remote_access.action(data)


def post_behavior_prompts(service, path, data, project_name):
    return behavior_prompts_api.post(service.home, data)


def post_admin_chat_send(service, path, data, project_name):
    return operations_chat.send(service.home, service.plugin_root, data)


def post_admin_chat_interrupt(service, path, data, project_name):
    return operations_chat.interrupt(service.home)


def post_admin_config(service, path, data, project_name):
    return operations_settings.save(service.home, service.plugin_root, data)


def post_connections_configure(service, path, data, project_name):
    return service.connections.configure(data)


def post_connections_action(service, path, data, project_name):
    return service.connections.action(data)


def post_connections_preferences(service, path, data, project_name):
    return service.connections.save_preferences(data)


def post_member_defaults(service, path, data, project_name):
    if set(data) != {'leader_session_id', 'selection'}:
        raise ValueError('组员默认设置必须包含 leader_session_id 和 selection')
    with service.write_lock, closing(store.connect(service.home)) as conn:
        project = service.selected_project(conn, project_name)
        sid = _text(data, 'leader_session_id')
        if board.session(conn, sid)['project'] != project:
            raise board.BoardError('leader 不属于当前工作区')
        return member_defaults.save(conn, sid, data['selection'], now=service.now().isoformat(timespec='milliseconds').replace('+00:00', 'Z'))


def post_sounds_custom(service, path, data, project_name):
    return event_sounds.add_custom(service.home, data)


def post_sounds_custom_delete(service, path, data, project_name):
    if not isinstance(data, dict) or set(data) != {'id'}:
        raise board.BoardError('删除自定义音效只需要 id')
    with service.write_lock, closing(store.connect(service.home)) as conn:
        return event_sounds.delete_custom(conn, service.home, data['id'])


def post_claude_budget(service, path, data, project_name):
    if set(data) != {'budget'}:
        raise ValueError('Claude 额度设置只能包含 budget')
    claude_budget.save(service.home, data['budget'])
    with service.scan_lock, closing(store.connect(service.home)) as conn:
        service.claude_budget = service._budget(conn, service.now())
    return {'budget': service.claude_budget}


def post_settings(service, path, data, project_name):
    if not data or not set(data) <= settings.SETTINGS_FIELDS:
        raise ValueError('设置请求包含无效字段')
    with service.write_lock, closing(store.connect(service.home)) as conn, goals.transaction(conn):
        if 'ai' in data:
            settings.save(conn, data['ai'])
        if 'execution' in data:
            settings.save_execution(conn, data['execution'])
        if 'sounds' in data:
            event_sounds.save(conn, data['sounds'], service.home)
        if 'canvas_wheel_zoom' in data:
            settings.save_canvas_wheel_zoom(conn, data['canvas_wheel_zoom'])
    return service.settings_state()


def get_remote_access(handler, service, url, query):
    handler._send(200, service.remote_access.snapshot())


def get_admin_connection_plan(handler, service, url, query):
    handler._send(200, service.connection_plan())


def get_admin_chat(handler, service, url, query):
    handler._send(200, operations_chat.snapshot(service.home))


def get_admin_config(handler, service, url, query):
    handler._send(200, operations_settings.snapshot(service.home, service.plugin_root))


def get_admin_health(handler, service, url, query):
    workers = {name: bool(worker and worker.is_alive()) for name, worker in (
        ('agents', service.worker), ('notices', service.notice_worker), ('git_ci', service.ci_worker))}
    handler._send(200, {'ok': True, 'service': 'crew', 'home': str(Path(service.home).resolve()),
                     'pid': os.getpid(), 'workers': workers,
                     'warnings': [name for name, running in workers.items() if not running],
                     'version': service.version, 'plugin_root': str(service.plugin_root.resolve()),
                     'instance_id': service.instance_id, 'host_name': socket.gethostname(), 'port': handler.server.server_port,
                     'update_gate': service.chat.paused() is not None})


def get_behavior_prompts(handler, service, url, query):
    handler._send(200, behavior_prompts_api.get(service.home, query))


def get_runtimes(handler, service, url, query):
    catalog = service.runtime_catalog.refresh(force=True) if query.get('refresh') == ['1'] else service.runtime_catalog.refresh()
    service.chat.changes.mark('runtimes', None, 'runtimes')
    handler._send(200, {'runtimes': catalog})


def get_runtime_readiness(handler, service, url, query):
    handler._send(200, {'runtimes': runtime_catalog.readiness()})


def get_onboarding(handler, service, url, query):
    handler._send(200, onboarding.get(service.home))


def post_onboarding(service, path, data, project_name):
    with service.write_lock, closing(store.connect(service.home)) as conn, goals.transaction(conn):
        return onboarding.save(conn, data)


def get_connections(handler, service, url, query):
    handler._send(200, service.connections.snapshot())


def get_settings(handler, service, url, query):
    handler._send(200, service.settings_state())


def get_usage(handler, service, url, query):
    period = query.get("period", ["week"])[0]
    if url.path.endswith("/export"):
        what = query.get("what", [None])[0]
        if what not in usage_cli.EXPORT_FIELDS:
            raise ValueError("\u5bfc\u51fa\u7c7b\u578b\u5fc5\u987b\u662f sessions / daily / codex / requests")
        rows = service.usage(period, what, query.get('project', [None])[0], query.get('scope', ['workspace'])[0])
        stream = io.StringIO(newline="")
        usage_cli.write_csv(stream, what, rows)
        handler._send(200, stream.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", "crew-" + what + ".csv")
    else:
        handler._send(200, service.usage(period, project_name=query.get('project', [None])[0], scope=query.get('scope', ['workspace'])[0]))
