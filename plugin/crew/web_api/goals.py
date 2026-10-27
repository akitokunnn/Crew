"""Goals HTTP interface; dependencies are supplied explicitly at composition."""
from .. import board, goal_api, goals
from .registry import Content


def register(routes, db, control):
    def post_mutate(request, conn, project, now):
        data, path = request.data, request.path
        return goal_api.post(control, conn, project, path, data)

    routes.add('POST', '/api/goals/create', db.command(post_mutate), model_work=True)
    routes.add('POST', '/api/goals/action', db.command(post_mutate), model_work=True)
    routes.add('POST', '/api/goals/context', db.command(post_mutate), model_work=True)
    routes.add('POST', '/api/goals/agent', db.command(post_mutate), model_work=True)

    def get_query(request):
        query = request.query
        with db.read() as conn:
            project = db.selected_project(conn, query.get('project', [None])[0])
            if request.path == '/api/goals':
                return {'goals': [goal_api.card(conn, goal) for goal in goals.list_goals(conn, project)]}
            elif request.path == '/api/goal':
                return goal_api.detail(conn, project, query.get('id', [''])[0])
            else:
                goal_id = query.get('goal_id', [''])[0]
                goals.get(conn, goal_id, project)
                report = goals.get_report(conn, query.get('report_id', [''])[0])
                if report['goal_id'] != goal_id:
                    raise board.BoardError('报告不属于此目标')
                return Content(report['html'].encode('utf-8'), 'text/html; charset=utf-8', interactive_report=True, sandbox=True)

    routes.add('GET', ['/api/goals', '/api/goal', '/api/goals/report'], get_query)
