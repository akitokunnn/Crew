"""Compose built-in HTTP features from the application's explicit capabilities.

Add a feature's register() call here. Paths, validation, response kinds and work
admission remain with that feature; the HTTP transport needs no new branch.
"""
from . import access, behavior, collaboration, conversations, documents, files, git, goals, maintenance, people, personal, service_control, sessions, settings, storage, tasks, usage, workspace_data, workspaces
from .database import Database
from .registry import Content, Download, Events, NotFound, Registry, Request, Response
from .. import approval_actions, conversation_queries, decision_actions


def build(service):
    db = Database(service.home, service.write_lock, service.now,
                  lambda conn, name: service.selected_project(conn, name))
    routes = Registry(service.chat.work)
    access.register(routes, snapshot=service.remote_access.public, configure=service.remote_access.configure,
        login=service.remote_access.login, logout=service.remote_access.logout)
    behavior.register(routes, db)
    service_control.register(routes, db)
    select_default = lambda name: setattr(service, 'default_project', name)
    workspace_data.register(routes, db,
        default_project=lambda: service.default_project, set_default_project=select_default,
        storage=service.storage, change_workspace=service.change_workspace,
        workspace_state=service.workspace_state)
    workspaces.register(routes, db,
        set_default_project=select_default, state=lambda name: service.state(name),
        storage=service.storage, change_workspace=service.change_workspace,
        workspace_activity_snapshot=service.activity().snapshot,
        workspace_state=service.workspace_state)
    settings.register(routes, db,
        connection_plan=service.connection_plan, connections=service.connections,
        health_snapshot=service.health_snapshot, plugin_root=service.plugin_root,
        runtime_catalog=service.runtime_catalog, set_away=service.set_away,
        settings_state=service.settings_state, prepare_update=service.prepare_update,
        cancel_update=service.cancel_update, prepare_connection=service.prepare_connection)
    sessions.register(routes, db,
        agents_error=lambda: service.agents_error, agents_lock=service.agents_lock,
        agents_provider=service.agents_provider, agents_ready=service.agents_ready,
        archive_session=service.archive_session, chat=service.chat,
        claude_root=service.claude_root, runtime_catalog=service.runtime_catalog)
    histories = conversation_queries.Queries(service.home, service.selected_project, service.chat.executions,
        service.chat.goal_controller.history, service.chat.goal_controller.current, service.chat.file)
    conversations.register(routes, db,
        chat=service.chat, chat_file=histories.file, chat_history=histories.history,
        chat_session=histories.session, resolve_native_approval=approval_actions.Approvals(service.chat.executions).resolve)
    library = service.library()
    people.register(routes, db,
        adopt=service.adopt, find_candidate=service.candidate, chat=service.chat,
        library_adopt=library.adopt,
        library_listing=library.listing,
        library_preview=library.preview,
        plugin_root=service.plugin_root, stop_archived=service.stop_archived)
    goals.register(routes, db, control=service.chat.goal_controller)
    from .. import task_scheduler
    task_control = service.chat.goal_controller
    personal.register(routes, db, pause=task_control.pause, agent_action=task_control.agent_action,
        send=service.chat.send, journal=service.chat.journal)
    tasks.register(routes, db, pause=task_control.pause, send=task_control.send,
                   stop_discussions=task_control.stop_discussions,
        schedule=task_control.schedule, scheduler_snapshot=task_scheduler.snapshot,
        notification_snapshot=service.task_notifications.snapshot)
    collaboration.register(routes, db,
        channel_digest=service.channel_digest, channel_messages=service.channel_messages,
        send_message=service.chat.send_message,
        decisions=decision_actions.Actions(service.chat.executions, service.chat.send,
            service.chat.goal_controller.send, service.decision_participants))
    documents.register(routes, db, document_list=service.document_list)
    files.register(routes, db, read_file=service.read_file, resolve_file=service.resolve_file)
    git.register(routes, db,
        chat=service.chat, git=service.git, git_commit=service.git_commit,
        git_settings=service.git_settings, plugin_root=service.plugin_root,
        stop_archived=service.stop_archived)
    storage.register(routes, db, storage_cleanup=service.storage_cleanup)
    maintenance.register(routes, db, manager=service.maintenance)
    usage.register(routes, db, usage=service.usage)

    return routes
