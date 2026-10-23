"""Track which role rules a session has read and prompt it after updates."""
import hashlib
from pathlib import Path

from . import board, store

SKILLS = {"crew:leader": "leader", "crew:member": "member"}


def digest(plugin_root, role):
    return hashlib.sha256((Path(plugin_root) / "skills" / role / "SKILL.md").read_bytes()).hexdigest()


@board.atomic
def record_read(conn, session_id, role, plugin_root, now=None):
    conn.execute("INSERT INTO rule_reads(session_id,role,digest,read_at) VALUES(?,?,?,?) "
                 "ON CONFLICT(session_id) DO UPDATE SET role=excluded.role,digest=excluded.digest,read_at=excluded.read_at",
                 (session_id, role, digest(plugin_root, role), store.now_iso() if now is None else now))


def reminder(conn, session_id, plugin_root):
    session = conn.execute("SELECT crew_name,role,status FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if (session is None or not session["crew_name"] or session["role"] not in SKILLS.values()
            or session["status"] == "closed" or board.is_archived(conn, session_id)):
        return None
    role = session["role"]
    current = digest(plugin_root, role)
    read = conn.execute("SELECT role,digest FROM rule_reads WHERE session_id=?", (session_id,)).fetchone()
    if read is not None and (read["role"], read["digest"]) == (role, current):
        return None
    return "Crew 守则（/crew:%s）有更新，你上次读的不是最新版。先调用 /crew:%s 重读一遍，再处理这条消息。" % (role, role)
