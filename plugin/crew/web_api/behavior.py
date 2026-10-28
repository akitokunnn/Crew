"""Settings inheritance and editable Crew prompts; no native execution on save."""
from .. import behavior_prompts, settings, tasks


def register(routes, db):
    def get_effective(request):
        with db.read() as conn:
            return settings.effective(conn, project=request.project, session_id=request.one('session_id'),
                                      purpose=request.one('purpose', 'default'))

    def get_scope(request):
        with db.read() as conn:
            return settings.get_scope(conn, request.one('scope', 'global'), request.one('identity'))

    def post_scope(request):
        data = request.data
        with db.write() as conn:
            return tasks.mutate(conn, request.path, data, lambda: settings.save_scope(conn,
                scope=data.get('scope', 'global'), identity=data.get('identity'), ai=data.get('ai'),
                execution=data.get('execution'), expected_version=data.get('version'), reset=data.get('reset', False)))

    def get_prompts(request):
        with db.read() as conn:
            if request.one('key'):
                return behavior_prompts.get(conn, request.one('key'), scope=request.one('scope', 'global'),
                                            identity=request.one('identity'))
            return behavior_prompts.resolve(conn, project=request.project, session_id=request.one('session_id'))

    def get_history(request):
        with db.read() as conn:
            return behavior_prompts.history(conn, request.one('key'), scope=request.one('scope', 'global'),
                identity=request.one('identity'), before=int(request.one('before')) if request.one('before') else None,
                limit=int(request.one('limit', '50')))

    def get_applied(request):
        with db.read() as conn:
            return behavior_prompts.inspect_applied(conn, request.one('execution_id'))

    def post_prompt(request):
        data = request.data
        options = {'scope': data.get('scope', 'global'), 'identity': data.get('identity'),
                   'expected_version': data.get('version')}
        with db.write() as conn:
            if request.path.endswith('/reset'):
                return tasks.mutate(conn, request.path, data, lambda: behavior_prompts.reset(conn, data.get('key'), **options))
            return tasks.mutate(conn, request.path, data, lambda: behavior_prompts.save(conn, data.get('key'), data.get('text'), **options))

    routes.add('GET', '/api/settings/effective', get_effective)
    routes.add('GET', '/api/settings/scope', get_scope)
    routes.add('POST', '/api/settings/scope', post_scope)
    routes.add('GET', '/api/prompts', get_prompts)
    routes.add('GET', '/api/prompts/history', get_history)
    routes.add('GET', '/api/prompts/applied', get_applied)
    routes.add('POST', ['/api/prompts/save', '/api/prompts/reset'], post_prompt)
