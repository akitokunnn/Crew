"""Which Crew role rules a session follows."""
from . import board


def skill_role(conn, session):
    """The role guide a session gets: assistant, git, leader or member."""
    from . import personal
    if personal.is_assistant(conn, session['session_id']):
        return 'assistant'
    config = board.git_config(conn, session["project"])
    if config is not None and config["admin_session_id"] == session["session_id"]:
        return "git"
    return session["role"]
