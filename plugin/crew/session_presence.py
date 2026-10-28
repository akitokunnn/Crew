"""Shared display state from the current process snapshot and owned journals."""
from . import board


def project_status(service, conn, project, agents, agents_error):
    agents = service.managed_agents(conn, agents)
    # Preserve stored display state while discovery is unavailable. Callers
    # must still report unknown rather than count that cache as observed work.
    identities = ([{'sessionId': person['session_id']} for person in board.sessions(conn, project)]
                  if agents_error else agents)
    return board.status(conn, project, identities), board.live_sessions(agents), agents


def status(chat, person, present, key):
    """Keep board hook freshness and the existing managed-journal precedence."""
    value = person['status']
    if chat.running(key):
        observed = chat.journal(key).status
        if value != 'waiting' or observed == 'working':
            value = observed
    elif person['session_id'] in present and present[person['session_id']].get('kind') == 'crew':
        observed = present[person['session_id']]['status']
        if value != 'waiting' or observed == 'busy':
            value = 'working' if observed == 'busy' else 'idle'
    return value
