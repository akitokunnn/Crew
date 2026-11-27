"""Call registry operations in tests as an AI session would through MCP, without the HTTP transport."""
import uuid

from crew import board, goals
from crew.ops import executor, registry, tokens
from crew.ops.caller import Caller


def agent_caller(conn, session_id, execution_id=None, runtime='claude'):
    """The caller and grants a Crew-launched session gets from its token."""
    role = tokens.role_of(conn, session_id, execution_id)
    session = board.session(conn, session_id)
    goal_id = goals.get_execution(conn, execution_id)['goal_id'] if execution_id else None
    caller = Caller(role, session_id=session_id, execution_id=execution_id, goal_id=goal_id,
                    project=session['project'] or None, runtime=runtime)
    return caller, frozenset(tokens.default_scopes(role))


def ai_call(service, conn, session_id, name, args=None, *, execution_id=None, runtime='claude',
            request_id=None, message_id=None):
    """Run operation ``name`` as session ``session_id``; writes get a fresh request id unless one is given."""
    caller, grants = agent_caller(conn, session_id, execution_id, runtime)
    op = registry.get(name) if registry.operations() else None
    if op is not None and op.access != 'read' and request_id is None:
        request_id = uuid.uuid4().hex
    return executor.call(service, caller, name, dict(args or {}), request_id=request_id, grants=grants,
                         message_id=message_id)
