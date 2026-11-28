"""Per-person personality for leaders and workspace assistants (docs/NEXT_BATCH_PLAN_2026-11-29.md M26–M39).

Stage-0 contract stub: the persona block fills these in; signatures are frozen.
"""


def input_frame(conn, session_id, source, room_id, identity):
    """A one-line marker for non-user input to a main session carrying a personality, or None."""
    return None


def note_input(conn, session_id, source, room_id, identity):
    """Count one accepted direct user message toward the next growth."""
