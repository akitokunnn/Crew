"""Global personal items and explicit user assistant operations."""
from .. import board, personal, tasks


def register(routes, db, *, pause, agent_action, send, journal):
    def open_assistant(request):
        with db.write() as conn:
            return {'assistant': personal.ensure_assistant(conn, request.data.get('project'))}

    def send_assistant(request):
        data = request.data
        if not isinstance(data.get('text', ''), str) or not data.get('text', '').strip() and not data.get('images'):
            raise ValueError('请提供文字或图片')
        with db.write() as conn:
            assistant = personal.ensure_assistant(conn, data.get('project'))
        current = journal(assistant['session_id'])
        if not current.path.exists():
            current.set_status('idle')
        return send(assistant, data.get('text', ''), identity=data.get('message_id'), images=data.get('images'))

    def native_action(request):
        data = request.data
        return agent_action('personal.' + str(data.get('action')), {
            'message_id': data.get('message_id'), 'request_id': data.get('request_id'),
            'data': data.get('data', {}), **({'project': data['data']['project']}
                if isinstance(data.get('data'), dict) and data['data'].get('project') is not None else {})},
            data.get('actor_session_id'), execution_id=data.get('execution_id'))
    def get_items(request):
        with db.read() as conn:
            return {'items': personal.list_items(conn, request.project,
                include_inactive=request.one('include_inactive') == '1', unassigned_only=request.one('unassigned') == '1'),
                'todos': personal.list_todos(conn, request.project, include_done=request.one('include_done') == '1')}

    def post_capture(request):
        data = request.data
        with db.write() as conn:
            return {'item': personal.capture(conn, data.get('text'), project=data.get('project'),
                tags=data.get('tags', []), request_id=data.get('request_id'), now=db.stamp())}

    def post_update(request):
        data = request.data
        fields = {key: data[key] for key in ('text', 'project', 'tags', 'state') if key in data}
        with db.write() as conn:
            return tasks.mutate(conn, request.path, data, lambda: {'item': personal.update_item(conn,
                data.get('id'), data.get('version'), **fields, now=db.stamp())})

    def post_todo(request):
        data = request.data
        with db.write() as conn:
            return tasks.mutate(conn, request.path, data, lambda: {'todo': personal.create_todo(conn,
                data.get('session_id'), data.get('title'), data.get('steps'), data.get('verification'),
                operation_key=data.get('operation_key'), task_ids=data.get('task_ids', []),
                blocking=data.get('blocking', False), actor=board.HUMAN, todo_id=data.get('todo_id'), tags=data.get('tags', []), now=db.stamp())})

    def post_link(request):
        data = request.data
        with db.write() as conn:
            return personal.link_item(conn, data.get('id'), data.get('version'), data.get('task_id'),
                request_id=data.get('request_id'), now=db.stamp())

    def post_chat(request):
        data = request.data
        with db.write() as conn:
            return personal.start_item_chat(conn, data.get('id'), data.get('version'), project=data.get('project'),
                task_id=data.get('task_id'), title=data.get('title'), new_task=data.get('new_task', False),
                request_id=data.get('request_id'), now=db.stamp())

    def post_todo_update(request):
        data = request.data
        with db.write() as conn:
            return {'todo': personal.update_todo(conn, data.get('id'), data.get('metadata_version'),
                tags=data.get('tags'), request_id=data.get('request_id'), now=db.stamp())}

    def post_complete(request):
        data = request.data
        with db.write() as conn:
            return personal.complete_todo(conn, data.get('id'), event_id=data.get('request_id'),
                expected_version=data.get('version'), note=data.get('note'), status=data.get('status', 'done'),
                attachments=data.get('attachments', []), home=db.home, now=db.stamp())

    def post_assistant(request):
        data = request.data
        # This route is an authenticated user command. Model tools use a
        # journal-backed source resolver, never a model-supplied context object.
        instruction = data.get('instruction')
        context = {'source': 'human', 'message_id': 'http:' + str(data.get('request_id')), 'text': instruction}
        with db.write() as conn:
            return personal.assistant_action(conn, data.get('action'), data.get('data', {}), instruction=instruction,
                context=context, request_id=data.get('request_id'), pause=pause)

    routes.add('GET', '/api/personal/items', get_items)
    routes.add('POST', '/api/assistant/open', open_assistant)
    routes.add('POST', '/api/assistant/send', send_assistant, model_work=True)
    routes.add('POST', '/api/assistant/native-action', native_action)
    routes.add('POST', '/api/personal/capture', post_capture)
    routes.add('POST', '/api/personal/update', post_update)
    routes.add('POST', '/api/personal/items/link', post_link)
    routes.add('POST', '/api/personal/items/chat', post_chat)
    routes.add('POST', '/api/personal/todos', post_todo)
    routes.add('POST', '/api/personal/todos/update', post_todo_update)
    routes.add('POST', '/api/personal/todos/complete', post_complete)
    routes.add('POST', '/api/assistant/action', post_assistant)
