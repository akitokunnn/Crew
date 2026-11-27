"""Importing this package registers every operation.

The user-facing domains register first; ``agents`` and ``agent_work`` then open
operations to AI roles and add the AI-only ones.
"""
from . import admin, board, campaign, confirmations, desktop_ipc, git, goals, personal, sessions, structure, war_room, workspace  # noqa: F401
from . import agents, agent_work  # noqa: F401  (must follow: they amend the operations above)
from . import assistant_cover  # noqa: F401  (last: opens every remaining user operation to the assistant)
