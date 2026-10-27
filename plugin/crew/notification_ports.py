"""Capabilities a remote inbox needs, independent of the web application.

Workspace facts and conversation commands are separate so changing a page or
carrier does not give a channel access to the whole application container.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkspaceQueries:
    clock: object
    attention: object
    observe_agents: object
    managed_agents: object


@dataclass(frozen=True)
class Conversations:
    executions: object
    admit: object
    send: object
    is_owned: object
