"""Goals and report preview HTTP routes."""
from contextlib import closing

from .. import board, goal_api, goals, report_documents, store


def post_report_worker(service, path, data, project_name):
    return service.report_previews.action(path.rsplit('/', 1)[-1], data)


def post_agent_report_preview(service, path, data, project_name):
    # This read-like operation waits on a desktop worker. Keep the
    # shared write lock free for that worker and unrelated projects.
    with closing(store.connect(service.home)) as conn:
        project = service.selected_project(conn, project_name)
        return goal_api.post(service, conn, project, path, data)


def post_goals(service, path, data, project_name, conn, now, project):
    return goal_api.post(service, conn, project, path, data)


def get_goals(handler, service, url, query):
    with closing(store.connect(service.home)) as conn:
        project = service.selected_project(conn, query.get('project', [None])[0])
        if url.path == '/api/goals':
            with service.chat.lock:
                approvals = service.chat.goal_controller.approvals()
            cards = [goal_api.card(conn, goal) for goal in goals.list_goals(conn, project)]
            waits = {card['id']: goal_api.waiting(card, approvals) for card in cards}
            handler._send(200, {'goals': cards, 'waiting': {key: value for key, value in waits.items() if value}})
        elif url.path == '/api/goal':
            identity = query.get('id', [''])[0]
            with service.chat.lock:
                approvals = service.chat.goal_controller.approvals(identity)
            handler._send(200, goal_api.detail(conn, project, identity, context_state=service.chat.context_state,
                                               approvals=approvals))
        else:
            goal_id = query.get('goal_id', [''])[0]
            goal = goals.get(conn, goal_id, project)
            report = goals.get_report(conn, query.get('report_id', [''])[0])
            if report['goal_id'] != goal_id:
                raise board.BoardError('报告不属于此目标')
            if query.get('format') == ['json']:
                handler._send(200, {key: value for key, value in report.items() if key != 'html'})
            else:
                report_format = report['document_format']
                if report_format == 'markdown':
                    # Only Crew-generated markup may run in the same-origin Markdown reader.
                    data = report['data']
                    content = report_documents.render_markdown(goal['title'], data['document_md'],
                                                               report_documents.verification(data))
                else:
                    content = report['html']
                handler.send_file_content(content.encode('utf-8'), 'text/html; charset=utf-8', report_format=report_format)
