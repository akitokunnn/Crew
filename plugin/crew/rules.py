"""Track which role rules a session has read and prompt it after updates."""
import hashlib
from pathlib import Path

from . import board, store

SKILLS = {"crew:leader": "leader", "crew:member": "member", "crew:git": "git", "crew:assistant": "assistant"}


def skill_role(conn, session):
    from . import personal
    if personal.is_assistant(conn, session['session_id']):
        return 'assistant'
    config = board.git_config(conn, session["project"])
    if config is not None and config["admin_session_id"] == session["session_id"]:
        return "git"
    return session["role"]


def digest(plugin_root, role):
    return hashlib.sha256((Path(plugin_root) / "skills" / role / "SKILL.md").read_bytes()).hexdigest()


@board.atomic
def record_read(conn, session_id, role, plugin_root, now=None):
    conn.execute("INSERT INTO rule_reads(session_id,role,digest,read_at) VALUES(?,?,?,?) "
                 "ON CONFLICT(session_id) DO UPDATE SET role=excluded.role,digest=excluded.digest,read_at=excluded.read_at",
                 (session_id, role, digest(plugin_root, role), store.now_iso() if now is None else now))


def reminder(conn, session_id, plugin_root):
    session = conn.execute("SELECT session_id,project,crew_name,role,status FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if (session is None or not session["crew_name"] or session["role"] not in SKILLS.values()
            or session["status"] == "closed" or board.is_archived(conn, session_id)):
        return None
    role = skill_role(conn, session)
    from . import behavior_prompts
    configured = behavior_prompts.resolve(conn, session_id=session_id, keys=(role,), plugin_root=plugin_root)
    if configured['prompts'][0]['source']['scope'] != 'default':
        # The editable role replaces this built-in skill. Manager delivers its
        # current version on the next input; rereading the old file contradicts it.
        return None
    current = digest(plugin_root, role)
    read = conn.execute("SELECT role,digest FROM rule_reads WHERE session_id=?", (session_id,)).fetchone()
    if read is not None and (read["role"], read["digest"]) == (role, current):
        return None
    return "Crew 守则（/crew:%s）有更新，你上次读的不是最新版。先调用 /crew:%s 重读一遍，再处理这条消息。" % (role, role)
