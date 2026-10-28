"""Remote login and configuration feature, separate from HTTP authorization."""
from .registry import Response


def register(routes, *, snapshot, configure, login, logout):
    def get_session(request):
        context = request.access or {}
        return {key: context.get(key, False if key != 'origin' else None)
                for key in ('local', 'authenticated', 'origin')}

    def post_login(request):
        body, cookie = login(request.data, request.access)
        return Response(body, headers=(('Set-Cookie', cookie),))

    def post_logout(request):
        body, cookie = logout(request.access)
        return Response(body, headers=(('Set-Cookie', cookie),))

    routes.add('GET', '/api/auth/session', get_session)
    routes.add('GET', '/api/remote', lambda request: snapshot())
    routes.add('POST', '/api/auth/login', post_login)
    routes.add('POST', '/api/auth/logout', post_logout)
    routes.add('POST', '/api/remote/configure', lambda request:
        configure(request.data, local=bool(request.access and request.access.get('local'))))
