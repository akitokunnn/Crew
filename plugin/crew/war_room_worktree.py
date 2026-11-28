"""Per-member git worktrees for war rooms (docs/NEXT_BATCH_PLAN_2026-11-29.md M1–M8).

Stage-0 contract stub: the war-room block fills these in; signatures are frozen.
"""


def ensure(conn, home, room_id):
    """Create pending worktrees of one room outside any transaction."""


def release(conn, room_id, session_id, now):
    """Mark one member's worktree as finished; cleanup happens in sweep()."""


def sweep(manager, conn, now):
    """Background loop step: create pending worktrees and clean finished ones."""


def path_for(conn, execution):
    """The worktree path a room-bound execution runs in, or None."""
    return None
