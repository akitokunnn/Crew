"""Event-triggered stall watch for war rooms (docs/NEXT_BATCH_PLAN_2026-11-29.md M19–M24).

Stage-0 contract stub: the war-room block fills these in; signatures are frozen.
"""


def note_turn(room_id):
    """Queue one room for a stall check after a turn or execution ended."""


def tick(manager, conn, live, now):
    """Background loop step: check queued rooms, wake the lead after the grace period."""
