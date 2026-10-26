-- Crew Storage V2: portable DOMAIN relations only (SQLite, design artifact).
-- Apply sqlite.sql before local.sql. Cloud protocol mirrors domain semantics in
-- PostgreSQL; cloud.sql contains server coordination, not a second domain model.
-- Every workspace relationship uses composite FKs; names and paths are not keys.
-- No command here opens or replaces any existing Crew runtime database.
-- Mutable writes use Repository CAS; immutable rows reject UPDATE below.
-- Controlled physical purge is deliberately separate from normal application SQL.
PRAGMA foreign_keys = ON;
PRAGMA user_version = 2;

BEGIN;

-- users: mutable; referenced global attribution/config.
-- Historical public attribution only; authentication subjects and credentials are not portable.
CREATE TABLE IF NOT EXISTS users (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    display_name TEXT NOT NULL,
    locale TEXT,
    PRIMARY KEY (id)
);

-- accounts: mutable; referenced global attribution/config.
-- Authorization tenant; membership snapshots do not themselves grant imported access.
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    kind TEXT NOT NULL CHECK (kind IN ('personal', 'team')),
    name TEXT NOT NULL,
    PRIMARY KEY (id)
);

-- account_memberships: mutable; referenced global attribution/config.
CREATE TABLE IF NOT EXISTS account_memberships (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    account_id TEXT NOT NULL REFERENCES accounts(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    PRIMARY KEY (id),
    UNIQUE (account_id, user_id)
);

-- user_settings: mutable; referenced global attribution/config.
-- Allowlisted nonsecret preferences; excluded from workspace parent-closure exports.
CREATE TABLE IF NOT EXISTS user_settings (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    user_id TEXT NOT NULL REFERENCES users(id),
    setting_key TEXT NOT NULL,
    value_json TEXT NOT NULL CHECK (json_valid(value_json)),
    PRIMARY KEY (id),
    UNIQUE (user_id, setting_key)
);

-- workspaces: mutable; workspace export root.
-- Permanent UUID root; names and local directory locations are not identity.
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    account_id TEXT NOT NULL REFERENCES accounts(id),
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    PRIMARY KEY (id),
    UNIQUE (account_id, id)
);

-- workspace_memberships: mutable; workspace export.
CREATE TABLE IF NOT EXISTS workspace_memberships (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    account_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, user_id),
    FOREIGN KEY (account_id, workspace_id) REFERENCES workspaces (account_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (account_id, user_id) REFERENCES account_memberships (account_id, user_id) DEFERRABLE INITIALLY DEFERRED
);

-- agents: mutable; workspace export.
-- Business identities, independent of provider, CLI session, process, or device.
CREATE TABLE IF NOT EXISTS agents (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'assistant' CHECK (kind IN ('assistant', 'git_administrator', 'system_administrator')),
    purpose TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id)
);

-- groups: mutable; workspace export.
CREATE TABLE IF NOT EXISTS groups (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    name TEXT NOT NULL,
    leader_agent_id TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, leader_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- group_memberships: mutable; workspace export.
-- Leadership is groups.leader_agent_id; membership role is not a runtime type.
CREATE TABLE IF NOT EXISTS group_memberships (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    group_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('member', 'reviewer')),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, group_id, agent_id),
    FOREIGN KEY (workspace_id, group_id) REFERENCES groups (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- blob_objects: mutable; workspace export.
-- Metadata only; physical key is workspace UUID/sha256. Digest and size never change.
CREATE TABLE IF NOT EXISTS blob_objects (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    sha256 TEXT NOT NULL CHECK (length(sha256)=64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    size_bytes INTEGER NOT NULL CHECK (size_bytes>=0),
    mime_type TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'declared' CHECK (state IN ('declared', 'verified', 'quarantined')),
    verified_at TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, sha256)
);

-- conversations: mutable; workspace export.
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    kind TEXT NOT NULL CHECK (kind IN ('direct', 'goal', 'channel', 'git', 'system')),
    title TEXT NOT NULL,
    owner_agent_id TEXT,
    group_id TEXT,
    goal_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, owner_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, group_id) REFERENCES groups (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, goal_id) REFERENCES goals (workspace_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- messages: immutable; workspace export.
-- Reply edges supply causal order. created_at is display metadata, not canonical ordering.
CREATE TABLE IF NOT EXISTS messages (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    conversation_id TEXT NOT NULL,
    reply_to_id TEXT,
    author_kind TEXT NOT NULL CHECK (author_kind IN ('user', 'agent', 'system')),
    author_user_id TEXT REFERENCES users(id),
    author_agent_id TEXT,
    author_name_snapshot TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('user', 'assistant', 'system', 'tool', 'peer', 'notice')),
    supersedes_message_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, conversation_id, id),
    FOREIGN KEY (workspace_id, conversation_id) REFERENCES conversations (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, conversation_id, reply_to_id) REFERENCES messages (workspace_id, conversation_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, conversation_id, supersedes_message_id) REFERENCES messages (workspace_id, conversation_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, author_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    CHECK ((author_kind='user' AND author_user_id IS NOT NULL AND author_agent_id IS NULL) OR (author_kind='agent' AND author_agent_id IS NOT NULL AND author_user_id IS NULL) OR (author_kind='system' AND author_user_id IS NULL AND author_agent_id IS NULL))
);

-- message_positions: immutable; workspace export.
-- Server-only accepted order; sequence is an ordinal, never an entity identity.
CREATE TABLE IF NOT EXISTS message_positions (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    conversation_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    accepted_sequence INTEGER NOT NULL CHECK (accepted_sequence>=1),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, conversation_id, accepted_sequence),
    UNIQUE (workspace_id, message_id),
    FOREIGN KEY (workspace_id, conversation_id, message_id) REFERENCES messages (workspace_id, conversation_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- message_parts: immutable; workspace export.
CREATE TABLE IF NOT EXISTS message_parts (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    message_id TEXT NOT NULL,
    part_index INTEGER NOT NULL CHECK (part_index>=0),
    kind TEXT NOT NULL CHECK (kind IN ('text', 'markdown', 'notice', 'json', 'image', 'file', 'tool_call', 'tool_result')),
    text_content TEXT,
    json_content TEXT CHECK (json_content IS NULL OR json_valid(json_content)),
    blob_id TEXT,
    artifact_version_id TEXT,
    tool_call_id TEXT,
    tool_result_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, message_id, part_index),
    FOREIGN KEY (workspace_id, message_id) REFERENCES messages (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, blob_id) REFERENCES blob_objects (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, artifact_version_id) REFERENCES artifact_versions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, message_id, tool_call_id) REFERENCES tool_calls (workspace_id, message_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, message_id, tool_result_id) REFERENCES tool_results (workspace_id, message_id, id) DEFERRABLE INITIALLY DEFERRED,
    CHECK ((kind IN ('text','markdown','notice') AND text_content IS NOT NULL AND json_content IS NULL AND blob_id IS NULL AND artifact_version_id IS NULL AND tool_call_id IS NULL AND tool_result_id IS NULL) OR (kind='json' AND json_content IS NOT NULL AND text_content IS NULL AND blob_id IS NULL AND artifact_version_id IS NULL AND tool_call_id IS NULL AND tool_result_id IS NULL) OR (kind IN ('image','file') AND (blob_id IS NOT NULL)+(artifact_version_id IS NOT NULL)=1 AND text_content IS NULL AND json_content IS NULL AND tool_call_id IS NULL AND tool_result_id IS NULL) OR (kind='tool_call' AND tool_call_id IS NOT NULL AND text_content IS NULL AND json_content IS NULL AND blob_id IS NULL AND artifact_version_id IS NULL AND tool_result_id IS NULL) OR (kind='tool_result' AND tool_result_id IS NOT NULL AND text_content IS NULL AND json_content IS NULL AND blob_id IS NULL AND artifact_version_id IS NULL AND tool_call_id IS NULL))
);

-- tool_calls: immutable; workspace export.
-- Visible tool invocation; native tool-call IDs and executable continuation tokens remain device-local.
CREATE TABLE IF NOT EXISTS tool_calls (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    conversation_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    display_label TEXT NOT NULL,
    arguments_json TEXT NOT NULL CHECK (json_valid(arguments_json)),
    arguments_blob_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, message_id, id),
    UNIQUE (workspace_id, conversation_id, id),
    FOREIGN KEY (workspace_id, conversation_id, message_id) REFERENCES messages (workspace_id, conversation_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, arguments_blob_id) REFERENCES blob_objects (workspace_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- tool_results: immutable; workspace export.
CREATE TABLE IF NOT EXISTS tool_results (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    conversation_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    result_index INTEGER NOT NULL CHECK (result_index>=0),
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'error', 'cancelled')),
    result_text TEXT,
    result_blob_id TEXT,
    artifact_version_id TEXT,
    is_truncated INTEGER NOT NULL DEFAULT 0 CHECK (is_truncated IN (0,1)),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, message_id, id),
    UNIQUE (workspace_id, tool_call_id, result_index),
    FOREIGN KEY (workspace_id, conversation_id, message_id) REFERENCES messages (workspace_id, conversation_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, conversation_id, tool_call_id) REFERENCES tool_calls (workspace_id, conversation_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, result_blob_id) REFERENCES blob_objects (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, artifact_version_id) REFERENCES artifact_versions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    CHECK (result_text IS NOT NULL OR result_blob_id IS NOT NULL OR artifact_version_id IS NOT NULL)
);

-- goals: mutable; workspace export.
CREATE TABLE IF NOT EXISTS goals (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    owner_agent_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'pausing', 'paused', 'waiting_user', 'awaiting_acceptance', 'completed', 'failed', 'cancelled')),
    current_requirement_id TEXT,
    current_round_id TEXT,
    priority INTEGER NOT NULL DEFAULT 0,
    manual_pause INTEGER NOT NULL DEFAULT 0 CHECK (manual_pause IN (0,1)),
    reason TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, owner_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, id, current_requirement_id) REFERENCES goal_requirements (workspace_id, goal_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, id, current_round_id) REFERENCES goal_rounds (workspace_id, goal_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- goal_dependencies: mutable; workspace export.
-- Repository rejects cycles; SQL prevents self-dependency and foreign workspace edges.
CREATE TABLE IF NOT EXISTS goal_dependencies (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    goal_id TEXT NOT NULL,
    requires_goal_id TEXT NOT NULL,
    required_outcome TEXT NOT NULL DEFAULT 'completed' CHECK (required_outcome IN ('completed', 'accepted')),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, goal_id, requires_goal_id),
    CHECK (goal_id<>requires_goal_id),
    FOREIGN KEY (workspace_id, goal_id) REFERENCES goals (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, requires_goal_id) REFERENCES goals (workspace_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- goal_requirements: immutable; workspace export.
CREATE TABLE IF NOT EXISTS goal_requirements (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    goal_id TEXT NOT NULL,
    version_no INTEGER NOT NULL CHECK (version_no>=1),
    request_markdown TEXT NOT NULL,
    acceptance_json TEXT NOT NULL CHECK (json_valid(acceptance_json)),
    constraints_json TEXT NOT NULL CHECK (json_valid(constraints_json)),
    change_summary TEXT NOT NULL,
    author_user_id TEXT REFERENCES users(id),
    author_agent_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, goal_id, id),
    UNIQUE (workspace_id, goal_id, version_no),
    FOREIGN KEY (workspace_id, goal_id) REFERENCES goals (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, author_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    CHECK (author_user_id IS NULL OR author_agent_id IS NULL)
);

-- goal_rounds: mutable; workspace export.
CREATE TABLE IF NOT EXISTS goal_rounds (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    goal_id TEXT NOT NULL,
    round_no INTEGER NOT NULL CHECK (round_no>=1),
    requirement_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'pausing', 'paused', 'waiting_user', 'awaiting_acceptance', 'completed', 'failed', 'cancelled')),
    outcome TEXT,
    reason TEXT,
    opened_at TEXT NOT NULL,
    ended_at TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, goal_id, id),
    UNIQUE (workspace_id, goal_id, round_no),
    FOREIGN KEY (workspace_id, goal_id) REFERENCES goals (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, goal_id, requirement_id) REFERENCES goal_requirements (workspace_id, goal_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- goal_assignments: mutable; workspace export.
CREATE TABLE IF NOT EXISTS goal_assignments (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    goal_round_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'contributor' CHECK (role IN ('responsible', 'contributor', 'reviewer')),
    scope_markdown TEXT NOT NULL,
    done_when_markdown TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'assigned' CHECK (status IN ('assigned', 'released')),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, goal_round_id, agent_id),
    UNIQUE (workspace_id, goal_round_id, agent_id, id),
    FOREIGN KEY (workspace_id, goal_round_id) REFERENCES goal_rounds (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- executions: mutable; workspace export.
-- Logical work attempt only; PID, native session IDs, command lines and run fences belong to local/cloud runtime tables.
CREATE TABLE IF NOT EXISTS executions (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    agent_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    goal_round_id TEXT,
    assignment_id TEXT,
    supersedes_execution_id TEXT,
    attempt_no INTEGER NOT NULL CHECK (attempt_no>=1),
    requested_model TEXT,
    requested_effort TEXT,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'pausing', 'paused', 'waiting_user', 'completed', 'failed', 'cancelled')),
    started_at TEXT,
    finished_at TEXT,
    error_summary TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, conversation_id) REFERENCES conversations (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, goal_round_id) REFERENCES goal_rounds (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, goal_round_id, agent_id, assignment_id) REFERENCES goal_assignments (workspace_id, goal_round_id, agent_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, supersedes_execution_id) REFERENCES executions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    CHECK (assignment_id IS NULL OR goal_round_id IS NOT NULL),
    UNIQUE (workspace_id, goal_round_id, id)
);

-- goal_acceptances: immutable; workspace export.
CREATE TABLE IF NOT EXISTS goal_acceptances (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    goal_round_id TEXT NOT NULL,
    report_id TEXT NOT NULL,
    accepted INTEGER NOT NULL CHECK (accepted IN (0,1)),
    note_markdown TEXT NOT NULL,
    author_user_id TEXT NOT NULL REFERENCES users(id),
    supersedes_acceptance_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, goal_round_id) REFERENCES goal_rounds (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, goal_round_id, report_id) REFERENCES reports (workspace_id, goal_round_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, supersedes_acceptance_id) REFERENCES goal_acceptances (workspace_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- reports: immutable; workspace export.
-- Unifies goal_reports and free reports. data_json has a versioned report document contract, not arbitrary runtime state.
CREATE TABLE IF NOT EXISTS reports (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    author_agent_id TEXT NOT NULL,
    goal_round_id TEXT,
    execution_id TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('goal_delivery', 'phase', 'summary', 'other')),
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('completed', 'failed', 'awaiting_acceptance', 'cancelled', 'informational')),
    body_markdown TEXT NOT NULL,
    data_json TEXT NOT NULL CHECK (json_valid(data_json)),
    html_artifact_version_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, goal_round_id, id),
    FOREIGN KEY (workspace_id, author_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, goal_round_id) REFERENCES goal_rounds (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, execution_id) REFERENCES executions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, html_artifact_version_id) REFERENCES artifact_versions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, goal_round_id, execution_id) REFERENCES executions (workspace_id, goal_round_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- artifacts: mutable; workspace export.
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('report', 'document', 'source', 'data', 'image', 'native_archive', 'legacy_archive', 'other')),
    description TEXT,
    current_version_id TEXT,
    pinned INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0,1)),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, id, current_version_id) REFERENCES artifact_versions (workspace_id, artifact_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- artifact_versions: immutable; workspace export.
-- Filename is display metadata, never an absolute executable/read path.
CREATE TABLE IF NOT EXISTS artifact_versions (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    artifact_id TEXT NOT NULL,
    version_no INTEGER NOT NULL CHECK (version_no>=1),
    blob_id TEXT NOT NULL,
    display_filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    summary TEXT,
    previous_version_id TEXT,
    author_user_id TEXT REFERENCES users(id),
    author_agent_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, artifact_id, id),
    UNIQUE (workspace_id, artifact_id, version_no),
    FOREIGN KEY (workspace_id, artifact_id) REFERENCES artifacts (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, blob_id) REFERENCES blob_objects (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, artifact_id, previous_version_id) REFERENCES artifact_versions (workspace_id, artifact_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, author_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    CHECK (author_user_id IS NULL OR author_agent_id IS NULL)
);

-- artifact_links: immutable; workspace export.
-- Typed target FKs; exactly one explicit target, never a polymorphic target_type/target_id business link.
CREATE TABLE IF NOT EXISTS artifact_links (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    artifact_version_id TEXT NOT NULL,
    relation TEXT NOT NULL DEFAULT 'attachment' CHECK (relation IN ('deliverable', 'evidence', 'attachment', 'reference')),
    goal_id TEXT,
    goal_round_id TEXT,
    execution_id TEXT,
    message_id TEXT,
    report_id TEXT,
    context_version_id TEXT,
    decision_id TEXT,
    todo_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, artifact_version_id) REFERENCES artifact_versions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, goal_id) REFERENCES goals (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, goal_round_id) REFERENCES goal_rounds (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, execution_id) REFERENCES executions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, message_id) REFERENCES messages (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, report_id) REFERENCES reports (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, context_version_id) REFERENCES context_versions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, decision_id) REFERENCES decisions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, todo_id) REFERENCES todos (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    CHECK ((goal_id IS NOT NULL)+(goal_round_id IS NOT NULL)+(execution_id IS NOT NULL)+(message_id IS NOT NULL)+(report_id IS NOT NULL)+(context_version_id IS NOT NULL)+(decision_id IS NOT NULL)+(todo_id IS NOT NULL)=1)
);

-- native_archives: immutable; workspace export.
-- Optional archive attachment. Crew history restore never promises native CLI resume on another device.
CREATE TABLE IF NOT EXISTS native_archives (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    agent_id TEXT,
    conversation_id TEXT,
    execution_id TEXT,
    artifact_version_id TEXT NOT NULL,
    archive_format TEXT NOT NULL,
    format_version TEXT NOT NULL,
    capabilities_json TEXT NOT NULL CHECK (json_valid(capabilities_json)),
    resume_guarantee TEXT NOT NULL DEFAULT 'none' CHECK (resume_guarantee='none'),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, conversation_id) REFERENCES conversations (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, execution_id) REFERENCES executions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, artifact_version_id) REFERENCES artifact_versions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    CHECK (agent_id IS NOT NULL OR conversation_id IS NOT NULL OR execution_id IS NOT NULL)
);

-- context_items: mutable; workspace export.
CREATE TABLE IF NOT EXISTS context_items (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    scope TEXT NOT NULL CHECK (scope IN ('workspace', 'agent', 'goal')),
    owner_agent_id TEXT,
    goal_id TEXT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    current_version_id TEXT,
    protected INTEGER NOT NULL DEFAULT 0 CHECK (protected IN (0,1)),
    state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'superseded', 'rejected')),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, owner_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, goal_id) REFERENCES goals (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, id, current_version_id) REFERENCES context_versions (workspace_id, context_item_id, id) DEFERRABLE INITIALLY DEFERRED,
    CHECK ((scope='workspace' AND owner_agent_id IS NULL AND goal_id IS NULL) OR (scope='agent' AND owner_agent_id IS NOT NULL AND goal_id IS NULL) OR (scope='goal' AND goal_id IS NOT NULL))
);

-- context_versions: immutable; workspace export.
CREATE TABLE IF NOT EXISTS context_versions (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    context_item_id TEXT NOT NULL,
    version_no INTEGER NOT NULL CHECK (version_no>=1),
    text_content TEXT,
    blob_id TEXT,
    change_reason TEXT,
    author_user_id TEXT REFERENCES users(id),
    author_agent_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, context_item_id, id),
    UNIQUE (workspace_id, context_item_id, version_no),
    FOREIGN KEY (workspace_id, context_item_id) REFERENCES context_items (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, blob_id) REFERENCES blob_objects (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, author_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    CHECK ((text_content IS NOT NULL)+(blob_id IS NOT NULL)=1),
    CHECK (author_user_id IS NULL OR author_agent_id IS NULL)
);

-- context_version_sources: immutable; workspace export.
CREATE TABLE IF NOT EXISTS context_version_sources (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    context_version_id TEXT NOT NULL,
    relation TEXT NOT NULL DEFAULT 'source' CHECK (relation IN ('source', 'depends_on', 'corrects')),
    message_id TEXT,
    report_id TEXT,
    artifact_version_id TEXT,
    source_context_version_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, context_version_id) REFERENCES context_versions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, message_id) REFERENCES messages (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, report_id) REFERENCES reports (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, artifact_version_id) REFERENCES artifact_versions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, source_context_version_id) REFERENCES context_versions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    CHECK ((message_id IS NOT NULL)+(report_id IS NOT NULL)+(artifact_version_id IS NOT NULL)+(source_context_version_id IS NOT NULL)=1)
);

-- context_manifests: immutable; workspace export.
-- Exact planned context; successful application is a separate immutable fact.
CREATE TABLE IF NOT EXISTS context_manifests (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    execution_id TEXT NOT NULL,
    requirement_id TEXT,
    summary_markdown TEXT NOT NULL,
    character_count INTEGER NOT NULL CHECK (character_count>=0),
    estimated_tokens INTEGER CHECK (estimated_tokens IS NULL OR estimated_tokens>=0),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, execution_id) REFERENCES executions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, requirement_id) REFERENCES goal_requirements (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    UNIQUE (workspace_id, execution_id, id)
);

-- context_manifest_entries: immutable; workspace export.
CREATE TABLE IF NOT EXISTS context_manifest_entries (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    manifest_id TEXT NOT NULL,
    context_version_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position>=0),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, manifest_id, position),
    UNIQUE (workspace_id, manifest_id, context_version_id),
    FOREIGN KEY (workspace_id, manifest_id) REFERENCES context_manifests (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, context_version_id) REFERENCES context_versions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- context_applications: immutable; workspace export.
CREATE TABLE IF NOT EXISTS context_applications (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    manifest_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    applied_character_count INTEGER NOT NULL CHECK (applied_character_count>=0),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, manifest_id, execution_id),
    FOREIGN KEY (workspace_id, manifest_id) REFERENCES context_manifests (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, execution_id) REFERENCES executions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, execution_id, manifest_id) REFERENCES context_manifests (workspace_id, execution_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- decisions: mutable; workspace export.
CREATE TABLE IF NOT EXISTS decisions (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    requester_agent_id TEXT NOT NULL,
    owner_leader_agent_id TEXT,
    goal_round_id TEXT,
    current_version_id TEXT,
    blocking INTEGER NOT NULL DEFAULT 0 CHECK (blocking IN (0,1)),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'open', 'answered', 'withdrawn')),
    held_at TEXT,
    expires_at TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, requester_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, owner_leader_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, goal_round_id) REFERENCES goal_rounds (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, id, current_version_id) REFERENCES decision_versions (workspace_id, decision_id, id) DEFERRABLE INITIALLY DEFERRED,
    CHECK (status='draft' OR current_version_id IS NOT NULL)
);

-- decision_versions: immutable; workspace export.
-- Frozen question content; status revisions and question versions are different.
CREATE TABLE IF NOT EXISTS decision_versions (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    decision_id TEXT NOT NULL,
    version_no INTEGER NOT NULL CHECK (version_no>=1),
    title TEXT NOT NULL,
    question_markdown TEXT NOT NULL,
    context_markdown TEXT NOT NULL,
    why_user TEXT NOT NULL,
    recommendation_reason TEXT,
    impact_markdown TEXT NOT NULL,
    author_user_id TEXT REFERENCES users(id),
    author_agent_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, decision_id, id),
    UNIQUE (workspace_id, decision_id, version_no),
    FOREIGN KEY (workspace_id, decision_id) REFERENCES decisions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, author_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    CHECK ((author_user_id IS NOT NULL)+(author_agent_id IS NOT NULL)=1)
);

-- decision_options: immutable; workspace export.
CREATE TABLE IF NOT EXISTS decision_options (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    decision_id TEXT NOT NULL,
    decision_version_id TEXT NOT NULL,
    option_key TEXT NOT NULL,
    label TEXT NOT NULL,
    consequences_markdown TEXT NOT NULL,
    recommended INTEGER NOT NULL DEFAULT 0 CHECK (recommended IN (0,1)),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, decision_id, decision_version_id, option_key),
    UNIQUE (workspace_id, decision_id, decision_version_id, id),
    FOREIGN KEY (workspace_id, decision_id) REFERENCES decisions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, decision_id, decision_version_id) REFERENCES decision_versions (workspace_id, decision_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- decision_answers: immutable; workspace export.
-- Server validates question revision and authorization before committing an answer.
CREATE TABLE IF NOT EXISTS decision_answers (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    decision_id TEXT NOT NULL,
    decision_version_id TEXT NOT NULL,
    decision_revision INTEGER NOT NULL CHECK (decision_revision>=1),
    option_id TEXT,
    answer_markdown TEXT NOT NULL,
    author_kind TEXT NOT NULL CHECK (author_kind IN ('user', 'agent')),
    author_user_id TEXT REFERENCES users(id),
    author_agent_id TEXT,
    authorization_policy_id TEXT,
    authorization_snapshot_json TEXT NOT NULL CHECK (json_valid(authorization_snapshot_json)),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, decision_id) REFERENCES decisions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, decision_id, decision_version_id) REFERENCES decision_versions (workspace_id, decision_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, decision_id, decision_version_id, option_id) REFERENCES decision_options (workspace_id, decision_id, decision_version_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, author_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, authorization_policy_id) REFERENCES policies (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    CHECK ((author_kind='user' AND author_user_id IS NOT NULL AND author_agent_id IS NULL) OR (author_kind='agent' AND author_agent_id IS NOT NULL AND author_user_id IS NULL))
);

-- todos: mutable; workspace export.
CREATE TABLE IF NOT EXISTS todos (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    requester_agent_id TEXT NOT NULL,
    assigned_user_id TEXT REFERENCES users(id),
    goal_round_id TEXT,
    title TEXT NOT NULL,
    steps_markdown TEXT NOT NULL,
    verification_markdown TEXT NOT NULL,
    blocking INTEGER NOT NULL DEFAULT 0 CHECK (blocking IN (0,1)),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'cancelled', 'withdrawn')),
    resolution_note TEXT,
    resolved_at TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, requester_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, goal_round_id) REFERENCES goal_rounds (workspace_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- peer_messages: mutable; workspace export.
-- Portable intent and acceptance, not permission to start a native process.
CREATE TABLE IF NOT EXISTS peer_messages (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    sender_agent_id TEXT NOT NULL,
    target_agent_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    goal_round_id TEXT,
    requires_reply INTEGER NOT NULL DEFAULT 0 CHECK (requires_reply IN (0,1)),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'cancelled', 'failed')),
    accepted_at TEXT,
    error_summary TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, sender_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, target_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, message_id) REFERENCES messages (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, goal_round_id) REFERENCES goal_rounds (workspace_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- handoffs: mutable; workspace export.
CREATE TABLE IF NOT EXISTS handoffs (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    sender_agent_id TEXT NOT NULL,
    target_agent_id TEXT,
    target_group_id TEXT,
    broadcast INTEGER NOT NULL DEFAULT 0 CHECK (broadcast IN (0,1)),
    goal_round_id TEXT,
    body_markdown TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, sender_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, target_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, target_group_id) REFERENCES groups (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, goal_round_id) REFERENCES goal_rounds (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    CHECK ((target_agent_id IS NOT NULL)+(target_group_id IS NOT NULL)+broadcast=1)
);

-- handoff_deliveries: immutable; workspace export.
CREATE TABLE IF NOT EXISTS handoff_deliveries (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    handoff_id TEXT NOT NULL,
    recipient_agent_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, handoff_id, recipient_agent_id),
    FOREIGN KEY (workspace_id, handoff_id) REFERENCES handoffs (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, recipient_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, message_id) REFERENCES messages (workspace_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- channels: mutable; workspace export.
CREATE TABLE IF NOT EXISTS channels (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    name TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    description TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, conversation_id),
    FOREIGN KEY (workspace_id, conversation_id) REFERENCES conversations (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    UNIQUE (workspace_id, conversation_id, id)
);

-- channel_posts: immutable; workspace export.
CREATE TABLE IF NOT EXISTS channel_posts (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    channel_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, message_id),
    FOREIGN KEY (workspace_id, channel_id) REFERENCES channels (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, message_id) REFERENCES messages (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    UNIQUE (workspace_id, channel_id, id),
    FOREIGN KEY (workspace_id, conversation_id, channel_id) REFERENCES channels (workspace_id, conversation_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, conversation_id, message_id) REFERENCES messages (workspace_id, conversation_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- channel_mentions: immutable; workspace export.
CREATE TABLE IF NOT EXISTS channel_mentions (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    post_id TEXT NOT NULL,
    target_agent_id TEXT NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, post_id, target_agent_id),
    FOREIGN KEY (workspace_id, post_id) REFERENCES channel_posts (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, target_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- channel_digests: immutable; workspace export.
-- Generated card with explicit source boundary; costs are usage_observations, never fabricated zeros.
CREATE TABLE IF NOT EXISTS channel_digests (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    channel_id TEXT NOT NULL,
    through_post_id TEXT NOT NULL,
    previous_digest_id TEXT,
    body_markdown TEXT NOT NULL,
    execution_id TEXT,
    source_message_count INTEGER NOT NULL CHECK (source_message_count>=0),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, channel_id) REFERENCES channels (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, through_post_id) REFERENCES channel_posts (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, previous_digest_id) REFERENCES channel_digests (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, execution_id) REFERENCES executions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    UNIQUE (workspace_id, channel_id, id),
    FOREIGN KEY (workspace_id, channel_id, through_post_id) REFERENCES channel_posts (workspace_id, channel_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, channel_id, previous_digest_id) REFERENCES channel_digests (workspace_id, channel_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- comments: mutable; workspace export.
CREATE TABLE IF NOT EXISTS comments (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    author_user_id TEXT REFERENCES users(id),
    author_agent_id TEXT,
    parent_comment_id TEXT,
    body_markdown TEXT NOT NULL,
    agent_id TEXT,
    group_id TEXT,
    goal_id TEXT,
    artifact_id TEXT,
    report_id TEXT,
    message_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, author_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, parent_comment_id) REFERENCES comments (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, group_id) REFERENCES groups (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, goal_id) REFERENCES goals (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, artifact_id) REFERENCES artifacts (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, report_id) REFERENCES reports (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, message_id) REFERENCES messages (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    CHECK ((author_user_id IS NOT NULL)+(author_agent_id IS NOT NULL)=1),
    CHECK ((agent_id IS NOT NULL)+(group_id IS NOT NULL)+(goal_id IS NOT NULL)+(artifact_id IS NOT NULL)+(report_id IS NOT NULL)+(message_id IS NOT NULL)=1)
);

-- trust_settings: mutable; workspace export.
-- Business delegation only; never grants shell permissions, credentials or device run rights.
CREATE TABLE IF NOT EXISTS trust_settings (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    leader_agent_id TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0,1)),
    goal_markdown TEXT NOT NULL,
    author_user_id TEXT NOT NULL REFERENCES users(id),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, leader_agent_id),
    FOREIGN KEY (workspace_id, leader_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- policies: mutable; workspace export.
-- Portable workflow rules and execution preferences; device paths and secrets are prohibited.
CREATE TABLE IF NOT EXISTS policies (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    policy_key TEXT NOT NULL,
    title TEXT NOT NULL,
    body_markdown TEXT NOT NULL,
    rules_json TEXT NOT NULL CHECK (json_valid(rules_json)),
    author_user_id TEXT REFERENCES users(id),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, policy_key)
);

-- git_requests: mutable; workspace export.
-- Git ref names and commit hashes are portable facts; checkout paths and credentials are local.
CREATE TABLE IF NOT EXISTS git_requests (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    requester_agent_id TEXT NOT NULL,
    administrator_agent_id TEXT,
    goal_round_id TEXT,
    title TEXT NOT NULL,
    description_markdown TEXT NOT NULL,
    source_ref TEXT,
    base_ref TEXT,
    source_commit TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'reviewing', 'waiting_user', 'accepted', 'rejected', 'completed', 'cancelled')),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, requester_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, administrator_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, goal_round_id) REFERENCES goal_rounds (workspace_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- git_results: immutable; workspace export.
CREATE TABLE IF NOT EXISTS git_results (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    request_id TEXT NOT NULL,
    author_agent_id TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('accepted', 'rejected', 'merged', 'failed', 'cancelled')),
    summary_markdown TEXT NOT NULL,
    result_commit TEXT,
    pull_request_url TEXT,
    checks_json TEXT NOT NULL CHECK (json_valid(checks_json)),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, request_id) REFERENCES git_requests (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, author_agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- usage_observations: immutable; workspace export.
-- Deduplicated observations, not accumulated counters. Unknown measurements stay NULL. Key is a source-scoped event digest, not a native session ID.
CREATE TABLE IF NOT EXISTS usage_observations (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    source_kind TEXT NOT NULL,
    source_observation_key TEXT NOT NULL,
    agent_id TEXT,
    execution_id TEXT,
    message_id TEXT,
    model TEXT,
    observed_at TEXT NOT NULL,
    input_tokens INTEGER CHECK (input_tokens IS NULL OR input_tokens>=0),
    cached_input_tokens INTEGER CHECK (cached_input_tokens IS NULL OR cached_input_tokens>=0),
    output_tokens INTEGER CHECK (output_tokens IS NULL OR output_tokens>=0),
    reasoning_tokens INTEGER CHECK (reasoning_tokens IS NULL OR reasoning_tokens>=0),
    cost_microunits INTEGER CHECK (cost_microunits IS NULL OR cost_microunits>=0),
    currency TEXT,
    measurement_json TEXT NOT NULL CHECK (json_valid(measurement_json)),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, source_kind, source_observation_key),
    FOREIGN KEY (workspace_id, agent_id) REFERENCES agents (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, execution_id) REFERENCES executions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, message_id) REFERENCES messages (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    CHECK (agent_id IS NOT NULL OR execution_id IS NOT NULL OR message_id IS NOT NULL),
    CHECK ((cost_microunits IS NULL AND currency IS NULL) OR (cost_microunits IS NOT NULL AND currency IS NOT NULL))
);

-- import_batches: mutable; workspace export.
-- Archive contains ONLY this workspace source rows and files. A complete multi-workspace source database stays in the private local migration vault.
CREATE TABLE IF NOT EXISTS import_batches (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    legacy_origin_id TEXT NOT NULL CHECK (length(legacy_origin_id)=36 AND substr(legacy_origin_id,9,1)='-' AND substr(legacy_origin_id,14,1)='-' AND substr(legacy_origin_id,19,1)='-' AND substr(legacy_origin_id,24,1)='-' AND length(replace(legacy_origin_id,'-',''))=32 AND replace(legacy_origin_id,'-','') NOT GLOB '*[^0-9a-f]*'),
    source_format TEXT NOT NULL,
    source_schema_version TEXT,
    archive_artifact_version_id TEXT NOT NULL,
    transform_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'staged' CHECK (status IN ('staged', 'verified', 'committed', 'failed')),
    verified_at TEXT,
    verification_summary TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, archive_artifact_version_id) REFERENCES artifact_versions (workspace_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- legacy_records: immutable; workspace export.
-- Typed source-key and transform dispositions are provenance annotations; mapping JSON never authorizes a business link.
CREATE TABLE IF NOT EXISTS legacy_records (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    import_batch_id TEXT NOT NULL,
    source_table TEXT NOT NULL,
    source_key_json TEXT NOT NULL CHECK (json_valid(source_key_json)),
    source_row_sha256 TEXT NOT NULL CHECK (length(source_row_sha256)=64 AND source_row_sha256 NOT GLOB '*[^0-9a-f]*'),
    raw_blob_id TEXT NOT NULL,
    disposition TEXT NOT NULL CHECK (disposition IN ('mapped', 'archive_only', 'unresolved', 'rejected')),
    mappings_json TEXT NOT NULL CHECK (json_valid(mappings_json)),
    notes TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, import_batch_id, source_table, source_key_json),
    FOREIGN KEY (workspace_id, import_batch_id) REFERENCES import_batches (workspace_id, id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (workspace_id, raw_blob_id) REFERENCES blob_objects (workspace_id, id) DEFERRABLE INITIALLY DEFERRED
);

-- entity_tombstones: mutable; workspace export.
-- Deletion mask for immutable and mutable records. Server validates entity type and workspace-scoped target; purge is separate controlled maintenance.
CREATE TABLE IF NOT EXISTS entity_tombstones (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*'),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??*Z'),
    updated_at TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??*Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at GLOB '????-??-??T??:??:??*Z'),
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL CHECK (length(entity_id)=36 AND substr(entity_id,9,1)='-' AND substr(entity_id,14,1)='-' AND substr(entity_id,19,1)='-' AND substr(entity_id,24,1)='-' AND length(replace(entity_id,'-',''))=32 AND replace(entity_id,'-','') NOT GLOB '*[^0-9a-f]*'),
    target_revision INTEGER NOT NULL CHECK (target_revision>=1),
    state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'restored')),
    target_deleted_at TEXT NOT NULL,
    operation_id TEXT NOT NULL CHECK (length(operation_id)=36 AND substr(operation_id,9,1)='-' AND substr(operation_id,14,1)='-' AND substr(operation_id,19,1)='-' AND substr(operation_id,24,1)='-' AND length(replace(operation_id,'-',''))=32 AND replace(operation_id,'-','') NOT GLOB '*[^0-9a-f]*'),
    reason TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, entity_type, entity_id)
);

-- Query and maintenance indexes. Workspace-prefix UNIQUE keys above also
-- support efficient scope scans and composite FK parent validation.
CREATE INDEX IF NOT EXISTS idx_agents_status ON agents (workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_groups_leader ON groups (workspace_id, leader_agent_id);
CREATE INDEX IF NOT EXISTS idx_conversations_goal ON conversations (workspace_id, goal_id);
CREATE INDEX IF NOT EXISTS idx_messages_causal ON messages (workspace_id, conversation_id, reply_to_id, id);
CREATE INDEX IF NOT EXISTS idx_goals_owner_status ON goals (workspace_id, owner_agent_id, status);
CREATE INDEX IF NOT EXISTS idx_assignments_agent ON goal_assignments (workspace_id, agent_id, status);
CREATE INDEX IF NOT EXISTS idx_executions_agent_status ON executions (workspace_id, agent_id, status);
CREATE INDEX IF NOT EXISTS idx_executions_round ON executions (workspace_id, goal_round_id);
CREATE INDEX IF NOT EXISTS idx_reports_round ON reports (workspace_id, goal_round_id, created_at);
CREATE INDEX IF NOT EXISTS idx_artifact_versions_blob ON artifact_versions (workspace_id, blob_id);
CREATE INDEX IF NOT EXISTS idx_artifact_links_goal ON artifact_links (workspace_id, goal_round_id);
CREATE INDEX IF NOT EXISTS idx_artifact_links_message ON artifact_links (workspace_id, message_id);
CREATE INDEX IF NOT EXISTS idx_context_items_scope ON context_items (workspace_id, scope, goal_id, owner_agent_id);
CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions (workspace_id, status, requester_agent_id);
CREATE INDEX IF NOT EXISTS idx_todos_status ON todos (workspace_id, status, blocking);
CREATE INDEX IF NOT EXISTS idx_peer_messages_target ON peer_messages (workspace_id, target_agent_id, status);
CREATE INDEX IF NOT EXISTS idx_handoffs_target ON handoffs (workspace_id, target_agent_id, target_group_id, status);
CREATE INDEX IF NOT EXISTS idx_channel_mentions_target ON channel_mentions (workspace_id, target_agent_id);
CREATE INDEX IF NOT EXISTS idx_comments_goal ON comments (workspace_id, goal_id);
CREATE INDEX IF NOT EXISTS idx_git_requests_status ON git_requests (workspace_id, status);
CREATE INDEX IF NOT EXISTS idx_usage_execution ON usage_observations (workspace_id, execution_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_usage_model_time ON usage_observations (workspace_id, model, observed_at);
CREATE INDEX IF NOT EXISTS idx_import_origin ON import_batches (workspace_id, legacy_origin_id);
CREATE INDEX IF NOT EXISTS idx_tombstones_state ON entity_tombstones (workspace_id, state, entity_type);

-- Enforced mutation boundaries. Clients still need revision CAS in WHERE;
-- a monotonic trigger alone cannot authorize a user or reject stale base data.
-- Incoming snapshots may jump revisions; accepted cloud mutations increment one.
CREATE TRIGGER IF NOT EXISTS guard_users_identity_revision
BEFORE UPDATE ON users
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
BEGIN
    SELECT RAISE(ABORT, 'users: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS guard_accounts_identity_revision
BEFORE UPDATE ON accounts
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
BEGIN
    SELECT RAISE(ABORT, 'accounts: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS guard_account_memberships_identity_revision
BEFORE UPDATE ON account_memberships
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
BEGIN
    SELECT RAISE(ABORT, 'account_memberships: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS guard_user_settings_identity_revision
BEFORE UPDATE ON user_settings
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
BEGIN
    SELECT RAISE(ABORT, 'user_settings: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS guard_workspaces_identity_revision
BEFORE UPDATE ON workspaces
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.account_id IS NOT OLD.account_id
BEGIN
    SELECT RAISE(ABORT, 'workspaces: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS guard_workspace_memberships_identity_revision
BEFORE UPDATE ON workspace_memberships
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'workspace_memberships: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS guard_agents_identity_revision
BEFORE UPDATE ON agents
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'agents: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS guard_groups_identity_revision
BEFORE UPDATE ON groups
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'groups: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS guard_group_memberships_identity_revision
BEFORE UPDATE ON group_memberships
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'group_memberships: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS guard_blob_objects_identity_revision
BEFORE UPDATE ON blob_objects
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
  OR NEW.sha256 IS NOT OLD.sha256
  OR NEW.size_bytes IS NOT OLD.size_bytes
BEGIN
    SELECT RAISE(ABORT, 'blob_objects: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS guard_conversations_identity_revision
BEFORE UPDATE ON conversations
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'conversations: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS immutable_messages_update
BEFORE UPDATE ON messages
BEGIN
    SELECT RAISE(ABORT, 'messages is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS immutable_message_positions_update
BEFORE UPDATE ON message_positions
BEGIN
    SELECT RAISE(ABORT, 'message_positions is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS immutable_message_parts_update
BEFORE UPDATE ON message_parts
BEGIN
    SELECT RAISE(ABORT, 'message_parts is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS immutable_tool_calls_update
BEFORE UPDATE ON tool_calls
BEGIN
    SELECT RAISE(ABORT, 'tool_calls is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS immutable_tool_results_update
BEFORE UPDATE ON tool_results
BEGIN
    SELECT RAISE(ABORT, 'tool_results is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS guard_goals_identity_revision
BEFORE UPDATE ON goals
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'goals: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS guard_goal_dependencies_identity_revision
BEFORE UPDATE ON goal_dependencies
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'goal_dependencies: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS immutable_goal_requirements_update
BEFORE UPDATE ON goal_requirements
BEGIN
    SELECT RAISE(ABORT, 'goal_requirements is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS guard_goal_rounds_identity_revision
BEFORE UPDATE ON goal_rounds
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'goal_rounds: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS guard_goal_assignments_identity_revision
BEFORE UPDATE ON goal_assignments
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'goal_assignments: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS guard_executions_identity_revision
BEFORE UPDATE ON executions
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'executions: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS immutable_goal_acceptances_update
BEFORE UPDATE ON goal_acceptances
BEGIN
    SELECT RAISE(ABORT, 'goal_acceptances is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS immutable_reports_update
BEFORE UPDATE ON reports
BEGIN
    SELECT RAISE(ABORT, 'reports is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS guard_artifacts_identity_revision
BEFORE UPDATE ON artifacts
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'artifacts: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS immutable_artifact_versions_update
BEFORE UPDATE ON artifact_versions
BEGIN
    SELECT RAISE(ABORT, 'artifact_versions is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS immutable_artifact_links_update
BEFORE UPDATE ON artifact_links
BEGIN
    SELECT RAISE(ABORT, 'artifact_links is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS immutable_native_archives_update
BEFORE UPDATE ON native_archives
BEGIN
    SELECT RAISE(ABORT, 'native_archives is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS guard_context_items_identity_revision
BEFORE UPDATE ON context_items
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'context_items: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS immutable_context_versions_update
BEFORE UPDATE ON context_versions
BEGIN
    SELECT RAISE(ABORT, 'context_versions is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS immutable_context_version_sources_update
BEFORE UPDATE ON context_version_sources
BEGIN
    SELECT RAISE(ABORT, 'context_version_sources is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS immutable_context_manifests_update
BEFORE UPDATE ON context_manifests
BEGIN
    SELECT RAISE(ABORT, 'context_manifests is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS immutable_context_manifest_entries_update
BEFORE UPDATE ON context_manifest_entries
BEGIN
    SELECT RAISE(ABORT, 'context_manifest_entries is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS immutable_context_applications_update
BEFORE UPDATE ON context_applications
BEGIN
    SELECT RAISE(ABORT, 'context_applications is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS guard_decisions_identity_revision
BEFORE UPDATE ON decisions
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'decisions: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS immutable_decision_versions_update
BEFORE UPDATE ON decision_versions
BEGIN
    SELECT RAISE(ABORT, 'decision_versions is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS immutable_decision_options_update
BEFORE UPDATE ON decision_options
BEGIN
    SELECT RAISE(ABORT, 'decision_options is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS immutable_decision_answers_update
BEFORE UPDATE ON decision_answers
BEGIN
    SELECT RAISE(ABORT, 'decision_answers is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS guard_todos_identity_revision
BEFORE UPDATE ON todos
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'todos: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS guard_peer_messages_identity_revision
BEFORE UPDATE ON peer_messages
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'peer_messages: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS guard_handoffs_identity_revision
BEFORE UPDATE ON handoffs
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'handoffs: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS immutable_handoff_deliveries_update
BEFORE UPDATE ON handoff_deliveries
BEGIN
    SELECT RAISE(ABORT, 'handoff_deliveries is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS guard_channels_identity_revision
BEFORE UPDATE ON channels
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'channels: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS immutable_channel_posts_update
BEFORE UPDATE ON channel_posts
BEGIN
    SELECT RAISE(ABORT, 'channel_posts is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS immutable_channel_mentions_update
BEFORE UPDATE ON channel_mentions
BEGIN
    SELECT RAISE(ABORT, 'channel_mentions is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS immutable_channel_digests_update
BEFORE UPDATE ON channel_digests
BEGIN
    SELECT RAISE(ABORT, 'channel_digests is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS guard_comments_identity_revision
BEFORE UPDATE ON comments
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'comments: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS guard_trust_settings_identity_revision
BEFORE UPDATE ON trust_settings
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'trust_settings: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS guard_policies_identity_revision
BEFORE UPDATE ON policies
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'policies: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS guard_git_requests_identity_revision
BEFORE UPDATE ON git_requests
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'git_requests: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS immutable_git_results_update
BEFORE UPDATE ON git_results
BEGIN
    SELECT RAISE(ABORT, 'git_results is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS immutable_usage_observations_update
BEFORE UPDATE ON usage_observations
BEGIN
    SELECT RAISE(ABORT, 'usage_observations is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS guard_import_batches_identity_revision
BEFORE UPDATE ON import_batches
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'import_batches: identity is fixed and revision must advance');
END;

CREATE TRIGGER IF NOT EXISTS immutable_legacy_records_update
BEFORE UPDATE ON legacy_records
BEGIN
    SELECT RAISE(ABORT, 'legacy_records is immutable; append a replacement or tombstone');
END;

CREATE TRIGGER IF NOT EXISTS guard_entity_tombstones_identity_revision
BEFORE UPDATE ON entity_tombstones
WHEN NEW.id IS NOT OLD.id
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS NOT OLD.workspace_id
BEGIN
    SELECT RAISE(ABORT, 'entity_tombstones: identity is fixed and revision must advance');
END;

COMMIT;

