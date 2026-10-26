-- Generated design DDL; run generate_postgres.py after sqlite.sql changes.

-- Source SHA-256: 1a5e82b6ce1615fa7ced8decd0a83516f47f8f6b7031366e19a1c16d5a1e7be1

-- Apply this DOMAIN file before cloud.sql in a dedicated database/schema.

-- UUIDs remain canonical TEXT in both engines; API UUID validation also applies.

-- No PostgreSQL server is contacted by this generator.

BEGIN;

CREATE OR REPLACE FUNCTION crew_json_valid(value TEXT) RETURNS BOOLEAN
LANGUAGE plpgsql IMMUTABLE STRICT AS $$
BEGIN
    PERFORM value::json;
    RETURN TRUE;
EXCEPTION WHEN invalid_text_representation THEN
    RETURN FALSE;
END;
$$;

CREATE TABLE IF NOT EXISTS users (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    display_name TEXT NOT NULL,
    locale TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    kind TEXT NOT NULL CHECK (kind IN ('personal', 'team')),
    name TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS account_memberships (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    account_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    PRIMARY KEY (id),
    UNIQUE (account_id, user_id)
);

CREATE TABLE IF NOT EXISTS user_settings (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    user_id TEXT NOT NULL,
    setting_key TEXT NOT NULL,
    value_json TEXT NOT NULL CHECK (crew_json_valid(value_json)),
    PRIMARY KEY (id),
    UNIQUE (user_id, setting_key)
);

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    account_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    PRIMARY KEY (id),
    UNIQUE (account_id, id)
);

CREATE TABLE IF NOT EXISTS workspace_memberships (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    account_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, user_id)
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'assistant' CHECK (kind IN ('assistant', 'git_administrator', 'system_administrator')),
    purpose TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id)
);

CREATE TABLE IF NOT EXISTS groups (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    name TEXT NOT NULL,
    leader_agent_id TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id)
);

CREATE TABLE IF NOT EXISTS group_memberships (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    group_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('member', 'reviewer')),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, group_id, agent_id)
);

CREATE TABLE IF NOT EXISTS blob_objects (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    sha256 TEXT NOT NULL CHECK (length(sha256)=64 AND sha256 !~ '[^0-9a-f]'),
    size_bytes BIGINT NOT NULL CHECK (size_bytes>=0),
    mime_type TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'declared' CHECK (state IN ('declared', 'verified', 'quarantined')),
    verified_at TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, sha256)
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    kind TEXT NOT NULL CHECK (kind IN ('direct', 'goal', 'channel', 'git', 'system')),
    title TEXT NOT NULL,
    owner_agent_id TEXT,
    group_id TEXT,
    goal_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id)
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    conversation_id TEXT NOT NULL,
    reply_to_id TEXT,
    author_kind TEXT NOT NULL CHECK (author_kind IN ('user', 'agent', 'system')),
    author_user_id TEXT,
    author_agent_id TEXT,
    author_name_snapshot TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('user', 'assistant', 'system', 'tool', 'peer', 'notice')),
    supersedes_message_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, conversation_id, id),
    CHECK ((author_kind='user' AND author_user_id IS NOT NULL AND author_agent_id IS NULL) OR (author_kind='agent' AND author_agent_id IS NOT NULL AND author_user_id IS NULL) OR (author_kind='system' AND author_user_id IS NULL AND author_agent_id IS NULL))
);

CREATE TABLE IF NOT EXISTS message_positions (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    conversation_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    accepted_sequence BIGINT NOT NULL CHECK (accepted_sequence>=1),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, conversation_id, accepted_sequence),
    UNIQUE (workspace_id, message_id)
);

CREATE TABLE IF NOT EXISTS message_parts (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    message_id TEXT NOT NULL,
    part_index BIGINT NOT NULL CHECK (part_index>=0),
    kind TEXT NOT NULL CHECK (kind IN ('text', 'markdown', 'notice', 'json', 'image', 'file', 'tool_call', 'tool_result')),
    text_content TEXT,
    json_content TEXT CHECK (json_content IS NULL OR crew_json_valid(json_content)),
    blob_id TEXT,
    artifact_version_id TEXT,
    tool_call_id TEXT,
    tool_result_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, message_id, part_index),
    CHECK ((kind IN ('text','markdown','notice') AND text_content IS NOT NULL AND json_content IS NULL AND blob_id IS NULL AND artifact_version_id IS NULL AND tool_call_id IS NULL AND tool_result_id IS NULL) OR (kind='json' AND json_content IS NOT NULL AND text_content IS NULL AND blob_id IS NULL AND artifact_version_id IS NULL AND tool_call_id IS NULL AND tool_result_id IS NULL) OR (kind IN ('image','file') AND (CASE WHEN blob_id IS NOT NULL THEN 1 ELSE 0 END)+(CASE WHEN artifact_version_id IS NOT NULL THEN 1 ELSE 0 END)=1 AND text_content IS NULL AND json_content IS NULL AND tool_call_id IS NULL AND tool_result_id IS NULL) OR (kind='tool_call' AND tool_call_id IS NOT NULL AND text_content IS NULL AND json_content IS NULL AND blob_id IS NULL AND artifact_version_id IS NULL AND tool_result_id IS NULL) OR (kind='tool_result' AND tool_result_id IS NOT NULL AND text_content IS NULL AND json_content IS NULL AND blob_id IS NULL AND artifact_version_id IS NULL AND tool_call_id IS NULL))
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    conversation_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    display_label TEXT NOT NULL,
    arguments_json TEXT NOT NULL CHECK (crew_json_valid(arguments_json)),
    arguments_blob_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, message_id, id),
    UNIQUE (workspace_id, conversation_id, id)
);

CREATE TABLE IF NOT EXISTS tool_results (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    conversation_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    result_index BIGINT NOT NULL CHECK (result_index>=0),
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'error', 'cancelled')),
    result_text TEXT,
    result_blob_id TEXT,
    artifact_version_id TEXT,
    is_truncated BIGINT NOT NULL DEFAULT 0 CHECK (is_truncated IN (0,1)),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, message_id, id),
    UNIQUE (workspace_id, tool_call_id, result_index),
    CHECK (result_text IS NOT NULL OR result_blob_id IS NOT NULL OR artifact_version_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS goals (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    owner_agent_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'pausing', 'paused', 'waiting_user', 'awaiting_acceptance', 'completed', 'failed', 'cancelled')),
    current_requirement_id TEXT,
    current_round_id TEXT,
    priority BIGINT NOT NULL DEFAULT 0,
    manual_pause BIGINT NOT NULL DEFAULT 0 CHECK (manual_pause IN (0,1)),
    reason TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id)
);

CREATE TABLE IF NOT EXISTS goal_dependencies (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    goal_id TEXT NOT NULL,
    requires_goal_id TEXT NOT NULL,
    required_outcome TEXT NOT NULL DEFAULT 'completed' CHECK (required_outcome IN ('completed', 'accepted')),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, goal_id, requires_goal_id),
    CHECK (goal_id<>requires_goal_id)
);

CREATE TABLE IF NOT EXISTS goal_requirements (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    goal_id TEXT NOT NULL,
    version_no BIGINT NOT NULL CHECK (version_no>=1),
    request_markdown TEXT NOT NULL,
    acceptance_json TEXT NOT NULL CHECK (crew_json_valid(acceptance_json)),
    constraints_json TEXT NOT NULL CHECK (crew_json_valid(constraints_json)),
    change_summary TEXT NOT NULL,
    author_user_id TEXT,
    author_agent_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, goal_id, id),
    UNIQUE (workspace_id, goal_id, version_no),
    CHECK (author_user_id IS NULL OR author_agent_id IS NULL)
);

CREATE TABLE IF NOT EXISTS goal_rounds (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    goal_id TEXT NOT NULL,
    round_no BIGINT NOT NULL CHECK (round_no>=1),
    requirement_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'pausing', 'paused', 'waiting_user', 'awaiting_acceptance', 'completed', 'failed', 'cancelled')),
    outcome TEXT,
    reason TEXT,
    opened_at TEXT NOT NULL,
    ended_at TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, goal_id, id),
    UNIQUE (workspace_id, goal_id, round_no)
);

CREATE TABLE IF NOT EXISTS goal_assignments (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    goal_round_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'contributor' CHECK (role IN ('responsible', 'contributor', 'reviewer')),
    scope_markdown TEXT NOT NULL,
    done_when_markdown TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'assigned' CHECK (status IN ('assigned', 'released')),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, goal_round_id, agent_id),
    UNIQUE (workspace_id, goal_round_id, agent_id, id)
);

CREATE TABLE IF NOT EXISTS executions (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    agent_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    goal_round_id TEXT,
    assignment_id TEXT,
    supersedes_execution_id TEXT,
    attempt_no BIGINT NOT NULL CHECK (attempt_no>=1),
    requested_model TEXT,
    requested_effort TEXT,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'pausing', 'paused', 'waiting_user', 'completed', 'failed', 'cancelled')),
    started_at TEXT,
    finished_at TEXT,
    error_summary TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    CHECK (assignment_id IS NULL OR goal_round_id IS NOT NULL),
    UNIQUE (workspace_id, goal_round_id, id)
);

CREATE TABLE IF NOT EXISTS goal_acceptances (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    goal_round_id TEXT NOT NULL,
    report_id TEXT NOT NULL,
    accepted BIGINT NOT NULL CHECK (accepted IN (0,1)),
    note_markdown TEXT NOT NULL,
    author_user_id TEXT NOT NULL,
    supersedes_acceptance_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id)
);

CREATE TABLE IF NOT EXISTS reports (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    author_agent_id TEXT NOT NULL,
    goal_round_id TEXT,
    execution_id TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('goal_delivery', 'phase', 'summary', 'other')),
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('completed', 'failed', 'awaiting_acceptance', 'cancelled', 'informational')),
    body_markdown TEXT NOT NULL,
    data_json TEXT NOT NULL CHECK (crew_json_valid(data_json)),
    html_artifact_version_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, goal_round_id, id)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('report', 'document', 'source', 'data', 'image', 'native_archive', 'legacy_archive', 'other')),
    description TEXT,
    current_version_id TEXT,
    pinned BIGINT NOT NULL DEFAULT 0 CHECK (pinned IN (0,1)),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id)
);

CREATE TABLE IF NOT EXISTS artifact_versions (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    artifact_id TEXT NOT NULL,
    version_no BIGINT NOT NULL CHECK (version_no>=1),
    blob_id TEXT NOT NULL,
    display_filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    summary TEXT,
    previous_version_id TEXT,
    author_user_id TEXT,
    author_agent_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, artifact_id, id),
    UNIQUE (workspace_id, artifact_id, version_no),
    CHECK (author_user_id IS NULL OR author_agent_id IS NULL)
);

CREATE TABLE IF NOT EXISTS artifact_links (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
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
    CHECK ((CASE WHEN goal_id IS NOT NULL THEN 1 ELSE 0 END)+(CASE WHEN goal_round_id IS NOT NULL THEN 1 ELSE 0 END)+(CASE WHEN execution_id IS NOT NULL THEN 1 ELSE 0 END)+(CASE WHEN message_id IS NOT NULL THEN 1 ELSE 0 END)+(CASE WHEN report_id IS NOT NULL THEN 1 ELSE 0 END)+(CASE WHEN context_version_id IS NOT NULL THEN 1 ELSE 0 END)+(CASE WHEN decision_id IS NOT NULL THEN 1 ELSE 0 END)+(CASE WHEN todo_id IS NOT NULL THEN 1 ELSE 0 END)=1)
);

CREATE TABLE IF NOT EXISTS native_archives (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    agent_id TEXT,
    conversation_id TEXT,
    execution_id TEXT,
    artifact_version_id TEXT NOT NULL,
    archive_format TEXT NOT NULL,
    format_version TEXT NOT NULL,
    capabilities_json TEXT NOT NULL CHECK (crew_json_valid(capabilities_json)),
    resume_guarantee TEXT NOT NULL DEFAULT 'none' CHECK (resume_guarantee='none'),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    CHECK (agent_id IS NOT NULL OR conversation_id IS NOT NULL OR execution_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS context_items (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    scope TEXT NOT NULL CHECK (scope IN ('workspace', 'agent', 'goal')),
    owner_agent_id TEXT,
    goal_id TEXT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    current_version_id TEXT,
    protected BIGINT NOT NULL DEFAULT 0 CHECK (protected IN (0,1)),
    state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'superseded', 'rejected')),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    CHECK ((scope='workspace' AND owner_agent_id IS NULL AND goal_id IS NULL) OR (scope='agent' AND owner_agent_id IS NOT NULL AND goal_id IS NULL) OR (scope='goal' AND goal_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS context_versions (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    context_item_id TEXT NOT NULL,
    version_no BIGINT NOT NULL CHECK (version_no>=1),
    text_content TEXT,
    blob_id TEXT,
    change_reason TEXT,
    author_user_id TEXT,
    author_agent_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, context_item_id, id),
    UNIQUE (workspace_id, context_item_id, version_no),
    CHECK ((CASE WHEN text_content IS NOT NULL THEN 1 ELSE 0 END)+(CASE WHEN blob_id IS NOT NULL THEN 1 ELSE 0 END)=1),
    CHECK (author_user_id IS NULL OR author_agent_id IS NULL)
);

CREATE TABLE IF NOT EXISTS context_version_sources (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    context_version_id TEXT NOT NULL,
    relation TEXT NOT NULL DEFAULT 'source' CHECK (relation IN ('source', 'depends_on', 'corrects')),
    message_id TEXT,
    report_id TEXT,
    artifact_version_id TEXT,
    source_context_version_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    CHECK ((CASE WHEN message_id IS NOT NULL THEN 1 ELSE 0 END)+(CASE WHEN report_id IS NOT NULL THEN 1 ELSE 0 END)+(CASE WHEN artifact_version_id IS NOT NULL THEN 1 ELSE 0 END)+(CASE WHEN source_context_version_id IS NOT NULL THEN 1 ELSE 0 END)=1)
);

CREATE TABLE IF NOT EXISTS context_manifests (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    execution_id TEXT NOT NULL,
    requirement_id TEXT,
    summary_markdown TEXT NOT NULL,
    character_count BIGINT NOT NULL CHECK (character_count>=0),
    estimated_tokens BIGINT CHECK (estimated_tokens IS NULL OR estimated_tokens>=0),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, execution_id, id)
);

CREATE TABLE IF NOT EXISTS context_manifest_entries (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    manifest_id TEXT NOT NULL,
    context_version_id TEXT NOT NULL,
    position BIGINT NOT NULL CHECK (position>=0),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, manifest_id, position),
    UNIQUE (workspace_id, manifest_id, context_version_id)
);

CREATE TABLE IF NOT EXISTS context_applications (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    manifest_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    applied_character_count BIGINT NOT NULL CHECK (applied_character_count>=0),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, manifest_id, execution_id)
);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    requester_agent_id TEXT NOT NULL,
    owner_leader_agent_id TEXT,
    goal_round_id TEXT,
    current_version_id TEXT,
    blocking BIGINT NOT NULL DEFAULT 0 CHECK (blocking IN (0,1)),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'open', 'answered', 'withdrawn')),
    held_at TEXT,
    expires_at TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    CHECK (status='draft' OR current_version_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS decision_versions (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    decision_id TEXT NOT NULL,
    version_no BIGINT NOT NULL CHECK (version_no>=1),
    title TEXT NOT NULL,
    question_markdown TEXT NOT NULL,
    context_markdown TEXT NOT NULL,
    why_user TEXT NOT NULL,
    recommendation_reason TEXT,
    impact_markdown TEXT NOT NULL,
    author_user_id TEXT,
    author_agent_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, decision_id, id),
    UNIQUE (workspace_id, decision_id, version_no),
    CHECK ((CASE WHEN author_user_id IS NOT NULL THEN 1 ELSE 0 END)+(CASE WHEN author_agent_id IS NOT NULL THEN 1 ELSE 0 END)=1)
);

CREATE TABLE IF NOT EXISTS decision_options (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    decision_id TEXT NOT NULL,
    decision_version_id TEXT NOT NULL,
    option_key TEXT NOT NULL,
    label TEXT NOT NULL,
    consequences_markdown TEXT NOT NULL,
    recommended BIGINT NOT NULL DEFAULT 0 CHECK (recommended IN (0,1)),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, decision_id, decision_version_id, option_key),
    UNIQUE (workspace_id, decision_id, decision_version_id, id)
);

CREATE TABLE IF NOT EXISTS decision_answers (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    decision_id TEXT NOT NULL,
    decision_version_id TEXT NOT NULL,
    decision_revision BIGINT NOT NULL CHECK (decision_revision>=1),
    option_id TEXT,
    answer_markdown TEXT NOT NULL,
    author_kind TEXT NOT NULL CHECK (author_kind IN ('user', 'agent')),
    author_user_id TEXT,
    author_agent_id TEXT,
    authorization_policy_id TEXT,
    authorization_snapshot_json TEXT NOT NULL CHECK (crew_json_valid(authorization_snapshot_json)),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    CHECK ((author_kind='user' AND author_user_id IS NOT NULL AND author_agent_id IS NULL) OR (author_kind='agent' AND author_agent_id IS NOT NULL AND author_user_id IS NULL))
);

CREATE TABLE IF NOT EXISTS todos (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    requester_agent_id TEXT NOT NULL,
    assigned_user_id TEXT,
    goal_round_id TEXT,
    title TEXT NOT NULL,
    steps_markdown TEXT NOT NULL,
    verification_markdown TEXT NOT NULL,
    blocking BIGINT NOT NULL DEFAULT 0 CHECK (blocking IN (0,1)),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'cancelled', 'withdrawn')),
    resolution_note TEXT,
    resolved_at TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id)
);

CREATE TABLE IF NOT EXISTS peer_messages (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    sender_agent_id TEXT NOT NULL,
    target_agent_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    goal_round_id TEXT,
    requires_reply BIGINT NOT NULL DEFAULT 0 CHECK (requires_reply IN (0,1)),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'cancelled', 'failed')),
    accepted_at TEXT,
    error_summary TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id)
);

CREATE TABLE IF NOT EXISTS handoffs (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    sender_agent_id TEXT NOT NULL,
    target_agent_id TEXT,
    target_group_id TEXT,
    broadcast BIGINT NOT NULL DEFAULT 0 CHECK (broadcast IN (0,1)),
    goal_round_id TEXT,
    body_markdown TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    CHECK ((CASE WHEN target_agent_id IS NOT NULL THEN 1 ELSE 0 END)+(CASE WHEN target_group_id IS NOT NULL THEN 1 ELSE 0 END)+broadcast=1)
);

CREATE TABLE IF NOT EXISTS handoff_deliveries (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    handoff_id TEXT NOT NULL,
    recipient_agent_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, handoff_id, recipient_agent_id)
);

CREATE TABLE IF NOT EXISTS channels (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    name TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    description TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, conversation_id),
    UNIQUE (workspace_id, conversation_id, id)
);

CREATE TABLE IF NOT EXISTS channel_posts (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    channel_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, message_id),
    UNIQUE (workspace_id, channel_id, id)
);

CREATE TABLE IF NOT EXISTS channel_mentions (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    post_id TEXT NOT NULL,
    target_agent_id TEXT NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, post_id, target_agent_id)
);

CREATE TABLE IF NOT EXISTS channel_digests (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    channel_id TEXT NOT NULL,
    through_post_id TEXT NOT NULL,
    previous_digest_id TEXT,
    body_markdown TEXT NOT NULL,
    execution_id TEXT,
    source_message_count BIGINT NOT NULL CHECK (source_message_count>=0),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, channel_id, id)
);

CREATE TABLE IF NOT EXISTS comments (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    author_user_id TEXT,
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
    CHECK ((CASE WHEN author_user_id IS NOT NULL THEN 1 ELSE 0 END)+(CASE WHEN author_agent_id IS NOT NULL THEN 1 ELSE 0 END)=1),
    CHECK ((CASE WHEN agent_id IS NOT NULL THEN 1 ELSE 0 END)+(CASE WHEN group_id IS NOT NULL THEN 1 ELSE 0 END)+(CASE WHEN goal_id IS NOT NULL THEN 1 ELSE 0 END)+(CASE WHEN artifact_id IS NOT NULL THEN 1 ELSE 0 END)+(CASE WHEN report_id IS NOT NULL THEN 1 ELSE 0 END)+(CASE WHEN message_id IS NOT NULL THEN 1 ELSE 0 END)=1)
);

CREATE TABLE IF NOT EXISTS trust_settings (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    leader_agent_id TEXT NOT NULL,
    enabled BIGINT NOT NULL CHECK (enabled IN (0,1)),
    goal_markdown TEXT NOT NULL,
    author_user_id TEXT NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, leader_agent_id)
);

CREATE TABLE IF NOT EXISTS policies (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    policy_key TEXT NOT NULL,
    title TEXT NOT NULL,
    body_markdown TEXT NOT NULL,
    rules_json TEXT NOT NULL CHECK (crew_json_valid(rules_json)),
    author_user_id TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, policy_key)
);

CREATE TABLE IF NOT EXISTS git_requests (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
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
    UNIQUE (workspace_id, id)
);

CREATE TABLE IF NOT EXISTS git_results (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    request_id TEXT NOT NULL,
    author_agent_id TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('accepted', 'rejected', 'merged', 'failed', 'cancelled')),
    summary_markdown TEXT NOT NULL,
    result_commit TEXT,
    pull_request_url TEXT,
    checks_json TEXT NOT NULL CHECK (crew_json_valid(checks_json)),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id)
);

CREATE TABLE IF NOT EXISTS usage_observations (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    source_kind TEXT NOT NULL,
    source_observation_key TEXT NOT NULL,
    agent_id TEXT,
    execution_id TEXT,
    message_id TEXT,
    model TEXT,
    observed_at TEXT NOT NULL,
    input_tokens BIGINT CHECK (input_tokens IS NULL OR input_tokens>=0),
    cached_input_tokens BIGINT CHECK (cached_input_tokens IS NULL OR cached_input_tokens>=0),
    output_tokens BIGINT CHECK (output_tokens IS NULL OR output_tokens>=0),
    reasoning_tokens BIGINT CHECK (reasoning_tokens IS NULL OR reasoning_tokens>=0),
    cost_microunits BIGINT CHECK (cost_microunits IS NULL OR cost_microunits>=0),
    currency TEXT,
    measurement_json TEXT NOT NULL CHECK (crew_json_valid(measurement_json)),
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, source_kind, source_observation_key),
    CHECK (agent_id IS NOT NULL OR execution_id IS NOT NULL OR message_id IS NOT NULL),
    CHECK ((cost_microunits IS NULL AND currency IS NULL) OR (cost_microunits IS NOT NULL AND currency IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS import_batches (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    legacy_origin_id TEXT NOT NULL CHECK (length(legacy_origin_id)=36 AND substr(legacy_origin_id,9,1)='-' AND substr(legacy_origin_id,14,1)='-' AND substr(legacy_origin_id,19,1)='-' AND substr(legacy_origin_id,24,1)='-' AND length(replace(legacy_origin_id,'-',''))=32 AND replace(legacy_origin_id,'-','') !~ '[^0-9a-f]'),
    source_format TEXT NOT NULL,
    source_schema_version TEXT,
    archive_artifact_version_id TEXT NOT NULL,
    transform_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'staged' CHECK (status IN ('staged', 'verified', 'committed', 'failed')),
    verified_at TEXT,
    verification_summary TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id)
);

CREATE TABLE IF NOT EXISTS legacy_records (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    import_batch_id TEXT NOT NULL,
    source_table TEXT NOT NULL,
    source_key_json TEXT NOT NULL CHECK (crew_json_valid(source_key_json)),
    source_row_sha256 TEXT NOT NULL CHECK (length(source_row_sha256)=64 AND source_row_sha256 !~ '[^0-9a-f]'),
    raw_blob_id TEXT NOT NULL,
    disposition TEXT NOT NULL CHECK (disposition IN ('mapped', 'archive_only', 'unresolved', 'rejected')),
    mappings_json TEXT NOT NULL CHECK (crew_json_valid(mappings_json)),
    notes TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, import_batch_id, source_table, source_key_json)
);

CREATE TABLE IF NOT EXISTS entity_tombstones (
    id TEXT NOT NULL CHECK (length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-' AND substr(id,19,1)='-' AND substr(id,24,1)='-' AND length(replace(id,'-',''))=32 AND replace(id,'-','') !~ '[^0-9a-f]'),
    workspace_id TEXT NOT NULL,
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision>=1),
    created_at TEXT NOT NULL CHECK (created_at LIKE '____-__-__T__:__:__%Z'),
    updated_at TEXT NOT NULL CHECK (updated_at LIKE '____-__-__T__:__:__%Z'),
    deleted_at TEXT CHECK (deleted_at IS NULL OR deleted_at LIKE '____-__-__T__:__:__%Z'),
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL CHECK (length(entity_id)=36 AND substr(entity_id,9,1)='-' AND substr(entity_id,14,1)='-' AND substr(entity_id,19,1)='-' AND substr(entity_id,24,1)='-' AND length(replace(entity_id,'-',''))=32 AND replace(entity_id,'-','') !~ '[^0-9a-f]'),
    target_revision BIGINT NOT NULL CHECK (target_revision>=1),
    state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'restored')),
    target_deleted_at TEXT NOT NULL,
    operation_id TEXT NOT NULL CHECK (length(operation_id)=36 AND substr(operation_id,9,1)='-' AND substr(operation_id,14,1)='-' AND substr(operation_id,19,1)='-' AND substr(operation_id,24,1)='-' AND length(replace(operation_id,'-',''))=32 AND replace(operation_id,'-','') !~ '[^0-9a-f]'),
    reason TEXT,
    PRIMARY KEY (id),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, entity_type, entity_id)
);

ALTER TABLE account_memberships ADD CONSTRAINT fk_account_memberships_0 FOREIGN KEY (user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE account_memberships ADD CONSTRAINT fk_account_memberships_1 FOREIGN KEY (account_id) REFERENCES accounts(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE user_settings ADD CONSTRAINT fk_user_settings_0 FOREIGN KEY (user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE workspaces ADD CONSTRAINT fk_workspaces_0 FOREIGN KEY (account_id) REFERENCES accounts(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE workspace_memberships ADD CONSTRAINT fk_workspace_memberships_0 FOREIGN KEY (account_id,user_id) REFERENCES account_memberships(account_id,user_id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE workspace_memberships ADD CONSTRAINT fk_workspace_memberships_1 FOREIGN KEY (account_id,workspace_id) REFERENCES workspaces(account_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE workspace_memberships ADD CONSTRAINT fk_workspace_memberships_2 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE agents ADD CONSTRAINT fk_agents_0 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE groups ADD CONSTRAINT fk_groups_0 FOREIGN KEY (workspace_id,leader_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE groups ADD CONSTRAINT fk_groups_1 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE group_memberships ADD CONSTRAINT fk_group_memberships_0 FOREIGN KEY (workspace_id,agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE group_memberships ADD CONSTRAINT fk_group_memberships_1 FOREIGN KEY (workspace_id,group_id) REFERENCES groups(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE group_memberships ADD CONSTRAINT fk_group_memberships_2 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE blob_objects ADD CONSTRAINT fk_blob_objects_0 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE conversations ADD CONSTRAINT fk_conversations_0 FOREIGN KEY (workspace_id,goal_id) REFERENCES goals(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE conversations ADD CONSTRAINT fk_conversations_1 FOREIGN KEY (workspace_id,group_id) REFERENCES groups(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE conversations ADD CONSTRAINT fk_conversations_2 FOREIGN KEY (workspace_id,owner_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE conversations ADD CONSTRAINT fk_conversations_3 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE messages ADD CONSTRAINT fk_messages_0 FOREIGN KEY (workspace_id,author_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE messages ADD CONSTRAINT fk_messages_1 FOREIGN KEY (workspace_id,conversation_id,supersedes_message_id) REFERENCES messages(workspace_id,conversation_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE messages ADD CONSTRAINT fk_messages_2 FOREIGN KEY (workspace_id,conversation_id,reply_to_id) REFERENCES messages(workspace_id,conversation_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE messages ADD CONSTRAINT fk_messages_3 FOREIGN KEY (workspace_id,conversation_id) REFERENCES conversations(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE messages ADD CONSTRAINT fk_messages_4 FOREIGN KEY (author_user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE messages ADD CONSTRAINT fk_messages_5 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE message_positions ADD CONSTRAINT fk_message_positions_0 FOREIGN KEY (workspace_id,conversation_id,message_id) REFERENCES messages(workspace_id,conversation_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE message_positions ADD CONSTRAINT fk_message_positions_1 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE message_parts ADD CONSTRAINT fk_message_parts_0 FOREIGN KEY (workspace_id,message_id,tool_result_id) REFERENCES tool_results(workspace_id,message_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE message_parts ADD CONSTRAINT fk_message_parts_1 FOREIGN KEY (workspace_id,message_id,tool_call_id) REFERENCES tool_calls(workspace_id,message_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE message_parts ADD CONSTRAINT fk_message_parts_2 FOREIGN KEY (workspace_id,artifact_version_id) REFERENCES artifact_versions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE message_parts ADD CONSTRAINT fk_message_parts_3 FOREIGN KEY (workspace_id,blob_id) REFERENCES blob_objects(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE message_parts ADD CONSTRAINT fk_message_parts_4 FOREIGN KEY (workspace_id,message_id) REFERENCES messages(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE message_parts ADD CONSTRAINT fk_message_parts_5 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE tool_calls ADD CONSTRAINT fk_tool_calls_0 FOREIGN KEY (workspace_id,arguments_blob_id) REFERENCES blob_objects(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE tool_calls ADD CONSTRAINT fk_tool_calls_1 FOREIGN KEY (workspace_id,conversation_id,message_id) REFERENCES messages(workspace_id,conversation_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE tool_calls ADD CONSTRAINT fk_tool_calls_2 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE tool_results ADD CONSTRAINT fk_tool_results_0 FOREIGN KEY (workspace_id,artifact_version_id) REFERENCES artifact_versions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE tool_results ADD CONSTRAINT fk_tool_results_1 FOREIGN KEY (workspace_id,result_blob_id) REFERENCES blob_objects(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE tool_results ADD CONSTRAINT fk_tool_results_2 FOREIGN KEY (workspace_id,conversation_id,tool_call_id) REFERENCES tool_calls(workspace_id,conversation_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE tool_results ADD CONSTRAINT fk_tool_results_3 FOREIGN KEY (workspace_id,conversation_id,message_id) REFERENCES messages(workspace_id,conversation_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE tool_results ADD CONSTRAINT fk_tool_results_4 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goals ADD CONSTRAINT fk_goals_0 FOREIGN KEY (workspace_id,id,current_round_id) REFERENCES goal_rounds(workspace_id,goal_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goals ADD CONSTRAINT fk_goals_1 FOREIGN KEY (workspace_id,id,current_requirement_id) REFERENCES goal_requirements(workspace_id,goal_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goals ADD CONSTRAINT fk_goals_2 FOREIGN KEY (workspace_id,owner_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goals ADD CONSTRAINT fk_goals_3 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goal_dependencies ADD CONSTRAINT fk_goal_dependencies_0 FOREIGN KEY (workspace_id,requires_goal_id) REFERENCES goals(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goal_dependencies ADD CONSTRAINT fk_goal_dependencies_1 FOREIGN KEY (workspace_id,goal_id) REFERENCES goals(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goal_dependencies ADD CONSTRAINT fk_goal_dependencies_2 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goal_requirements ADD CONSTRAINT fk_goal_requirements_0 FOREIGN KEY (workspace_id,author_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goal_requirements ADD CONSTRAINT fk_goal_requirements_1 FOREIGN KEY (workspace_id,goal_id) REFERENCES goals(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goal_requirements ADD CONSTRAINT fk_goal_requirements_2 FOREIGN KEY (author_user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goal_requirements ADD CONSTRAINT fk_goal_requirements_3 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goal_rounds ADD CONSTRAINT fk_goal_rounds_0 FOREIGN KEY (workspace_id,goal_id,requirement_id) REFERENCES goal_requirements(workspace_id,goal_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goal_rounds ADD CONSTRAINT fk_goal_rounds_1 FOREIGN KEY (workspace_id,goal_id) REFERENCES goals(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goal_rounds ADD CONSTRAINT fk_goal_rounds_2 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goal_assignments ADD CONSTRAINT fk_goal_assignments_0 FOREIGN KEY (workspace_id,agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goal_assignments ADD CONSTRAINT fk_goal_assignments_1 FOREIGN KEY (workspace_id,goal_round_id) REFERENCES goal_rounds(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goal_assignments ADD CONSTRAINT fk_goal_assignments_2 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE executions ADD CONSTRAINT fk_executions_0 FOREIGN KEY (workspace_id,supersedes_execution_id) REFERENCES executions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE executions ADD CONSTRAINT fk_executions_1 FOREIGN KEY (workspace_id,goal_round_id,agent_id,assignment_id) REFERENCES goal_assignments(workspace_id,goal_round_id,agent_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE executions ADD CONSTRAINT fk_executions_2 FOREIGN KEY (workspace_id,goal_round_id) REFERENCES goal_rounds(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE executions ADD CONSTRAINT fk_executions_3 FOREIGN KEY (workspace_id,conversation_id) REFERENCES conversations(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE executions ADD CONSTRAINT fk_executions_4 FOREIGN KEY (workspace_id,agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE executions ADD CONSTRAINT fk_executions_5 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goal_acceptances ADD CONSTRAINT fk_goal_acceptances_0 FOREIGN KEY (workspace_id,supersedes_acceptance_id) REFERENCES goal_acceptances(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goal_acceptances ADD CONSTRAINT fk_goal_acceptances_1 FOREIGN KEY (workspace_id,goal_round_id,report_id) REFERENCES reports(workspace_id,goal_round_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goal_acceptances ADD CONSTRAINT fk_goal_acceptances_2 FOREIGN KEY (workspace_id,goal_round_id) REFERENCES goal_rounds(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goal_acceptances ADD CONSTRAINT fk_goal_acceptances_3 FOREIGN KEY (author_user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE goal_acceptances ADD CONSTRAINT fk_goal_acceptances_4 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE reports ADD CONSTRAINT fk_reports_0 FOREIGN KEY (workspace_id,goal_round_id,execution_id) REFERENCES executions(workspace_id,goal_round_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE reports ADD CONSTRAINT fk_reports_1 FOREIGN KEY (workspace_id,html_artifact_version_id) REFERENCES artifact_versions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE reports ADD CONSTRAINT fk_reports_2 FOREIGN KEY (workspace_id,execution_id) REFERENCES executions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE reports ADD CONSTRAINT fk_reports_3 FOREIGN KEY (workspace_id,goal_round_id) REFERENCES goal_rounds(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE reports ADD CONSTRAINT fk_reports_4 FOREIGN KEY (workspace_id,author_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE reports ADD CONSTRAINT fk_reports_5 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE artifacts ADD CONSTRAINT fk_artifacts_0 FOREIGN KEY (workspace_id,id,current_version_id) REFERENCES artifact_versions(workspace_id,artifact_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE artifacts ADD CONSTRAINT fk_artifacts_1 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE artifact_versions ADD CONSTRAINT fk_artifact_versions_0 FOREIGN KEY (workspace_id,author_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE artifact_versions ADD CONSTRAINT fk_artifact_versions_1 FOREIGN KEY (workspace_id,artifact_id,previous_version_id) REFERENCES artifact_versions(workspace_id,artifact_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE artifact_versions ADD CONSTRAINT fk_artifact_versions_2 FOREIGN KEY (workspace_id,blob_id) REFERENCES blob_objects(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE artifact_versions ADD CONSTRAINT fk_artifact_versions_3 FOREIGN KEY (workspace_id,artifact_id) REFERENCES artifacts(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE artifact_versions ADD CONSTRAINT fk_artifact_versions_4 FOREIGN KEY (author_user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE artifact_versions ADD CONSTRAINT fk_artifact_versions_5 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE artifact_links ADD CONSTRAINT fk_artifact_links_0 FOREIGN KEY (workspace_id,todo_id) REFERENCES todos(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE artifact_links ADD CONSTRAINT fk_artifact_links_1 FOREIGN KEY (workspace_id,decision_id) REFERENCES decisions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE artifact_links ADD CONSTRAINT fk_artifact_links_2 FOREIGN KEY (workspace_id,context_version_id) REFERENCES context_versions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE artifact_links ADD CONSTRAINT fk_artifact_links_3 FOREIGN KEY (workspace_id,report_id) REFERENCES reports(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE artifact_links ADD CONSTRAINT fk_artifact_links_4 FOREIGN KEY (workspace_id,message_id) REFERENCES messages(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE artifact_links ADD CONSTRAINT fk_artifact_links_5 FOREIGN KEY (workspace_id,execution_id) REFERENCES executions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE artifact_links ADD CONSTRAINT fk_artifact_links_6 FOREIGN KEY (workspace_id,goal_round_id) REFERENCES goal_rounds(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE artifact_links ADD CONSTRAINT fk_artifact_links_7 FOREIGN KEY (workspace_id,goal_id) REFERENCES goals(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE artifact_links ADD CONSTRAINT fk_artifact_links_8 FOREIGN KEY (workspace_id,artifact_version_id) REFERENCES artifact_versions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE artifact_links ADD CONSTRAINT fk_artifact_links_9 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE native_archives ADD CONSTRAINT fk_native_archives_0 FOREIGN KEY (workspace_id,artifact_version_id) REFERENCES artifact_versions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE native_archives ADD CONSTRAINT fk_native_archives_1 FOREIGN KEY (workspace_id,execution_id) REFERENCES executions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE native_archives ADD CONSTRAINT fk_native_archives_2 FOREIGN KEY (workspace_id,conversation_id) REFERENCES conversations(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE native_archives ADD CONSTRAINT fk_native_archives_3 FOREIGN KEY (workspace_id,agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE native_archives ADD CONSTRAINT fk_native_archives_4 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_items ADD CONSTRAINT fk_context_items_0 FOREIGN KEY (workspace_id,id,current_version_id) REFERENCES context_versions(workspace_id,context_item_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_items ADD CONSTRAINT fk_context_items_1 FOREIGN KEY (workspace_id,goal_id) REFERENCES goals(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_items ADD CONSTRAINT fk_context_items_2 FOREIGN KEY (workspace_id,owner_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_items ADD CONSTRAINT fk_context_items_3 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_versions ADD CONSTRAINT fk_context_versions_0 FOREIGN KEY (workspace_id,author_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_versions ADD CONSTRAINT fk_context_versions_1 FOREIGN KEY (workspace_id,blob_id) REFERENCES blob_objects(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_versions ADD CONSTRAINT fk_context_versions_2 FOREIGN KEY (workspace_id,context_item_id) REFERENCES context_items(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_versions ADD CONSTRAINT fk_context_versions_3 FOREIGN KEY (author_user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_versions ADD CONSTRAINT fk_context_versions_4 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_version_sources ADD CONSTRAINT fk_context_version_sources_0 FOREIGN KEY (workspace_id,source_context_version_id) REFERENCES context_versions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_version_sources ADD CONSTRAINT fk_context_version_sources_1 FOREIGN KEY (workspace_id,artifact_version_id) REFERENCES artifact_versions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_version_sources ADD CONSTRAINT fk_context_version_sources_2 FOREIGN KEY (workspace_id,report_id) REFERENCES reports(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_version_sources ADD CONSTRAINT fk_context_version_sources_3 FOREIGN KEY (workspace_id,message_id) REFERENCES messages(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_version_sources ADD CONSTRAINT fk_context_version_sources_4 FOREIGN KEY (workspace_id,context_version_id) REFERENCES context_versions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_version_sources ADD CONSTRAINT fk_context_version_sources_5 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_manifests ADD CONSTRAINT fk_context_manifests_0 FOREIGN KEY (workspace_id,requirement_id) REFERENCES goal_requirements(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_manifests ADD CONSTRAINT fk_context_manifests_1 FOREIGN KEY (workspace_id,execution_id) REFERENCES executions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_manifests ADD CONSTRAINT fk_context_manifests_2 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_manifest_entries ADD CONSTRAINT fk_context_manifest_entries_0 FOREIGN KEY (workspace_id,context_version_id) REFERENCES context_versions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_manifest_entries ADD CONSTRAINT fk_context_manifest_entries_1 FOREIGN KEY (workspace_id,manifest_id) REFERENCES context_manifests(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_manifest_entries ADD CONSTRAINT fk_context_manifest_entries_2 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_applications ADD CONSTRAINT fk_context_applications_0 FOREIGN KEY (workspace_id,execution_id,manifest_id) REFERENCES context_manifests(workspace_id,execution_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_applications ADD CONSTRAINT fk_context_applications_1 FOREIGN KEY (workspace_id,execution_id) REFERENCES executions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_applications ADD CONSTRAINT fk_context_applications_2 FOREIGN KEY (workspace_id,manifest_id) REFERENCES context_manifests(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE context_applications ADD CONSTRAINT fk_context_applications_3 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE decisions ADD CONSTRAINT fk_decisions_0 FOREIGN KEY (workspace_id,id,current_version_id) REFERENCES decision_versions(workspace_id,decision_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE decisions ADD CONSTRAINT fk_decisions_1 FOREIGN KEY (workspace_id,goal_round_id) REFERENCES goal_rounds(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE decisions ADD CONSTRAINT fk_decisions_2 FOREIGN KEY (workspace_id,owner_leader_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE decisions ADD CONSTRAINT fk_decisions_3 FOREIGN KEY (workspace_id,requester_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE decisions ADD CONSTRAINT fk_decisions_4 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE decision_versions ADD CONSTRAINT fk_decision_versions_0 FOREIGN KEY (workspace_id,author_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE decision_versions ADD CONSTRAINT fk_decision_versions_1 FOREIGN KEY (workspace_id,decision_id) REFERENCES decisions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE decision_versions ADD CONSTRAINT fk_decision_versions_2 FOREIGN KEY (author_user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE decision_versions ADD CONSTRAINT fk_decision_versions_3 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE decision_options ADD CONSTRAINT fk_decision_options_0 FOREIGN KEY (workspace_id,decision_id,decision_version_id) REFERENCES decision_versions(workspace_id,decision_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE decision_options ADD CONSTRAINT fk_decision_options_1 FOREIGN KEY (workspace_id,decision_id) REFERENCES decisions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE decision_options ADD CONSTRAINT fk_decision_options_2 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE decision_answers ADD CONSTRAINT fk_decision_answers_0 FOREIGN KEY (workspace_id,authorization_policy_id) REFERENCES policies(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE decision_answers ADD CONSTRAINT fk_decision_answers_1 FOREIGN KEY (workspace_id,author_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE decision_answers ADD CONSTRAINT fk_decision_answers_2 FOREIGN KEY (workspace_id,decision_id,decision_version_id,option_id) REFERENCES decision_options(workspace_id,decision_id,decision_version_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE decision_answers ADD CONSTRAINT fk_decision_answers_3 FOREIGN KEY (workspace_id,decision_id,decision_version_id) REFERENCES decision_versions(workspace_id,decision_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE decision_answers ADD CONSTRAINT fk_decision_answers_4 FOREIGN KEY (workspace_id,decision_id) REFERENCES decisions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE decision_answers ADD CONSTRAINT fk_decision_answers_5 FOREIGN KEY (author_user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE decision_answers ADD CONSTRAINT fk_decision_answers_6 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE todos ADD CONSTRAINT fk_todos_0 FOREIGN KEY (workspace_id,goal_round_id) REFERENCES goal_rounds(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE todos ADD CONSTRAINT fk_todos_1 FOREIGN KEY (workspace_id,requester_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE todos ADD CONSTRAINT fk_todos_2 FOREIGN KEY (assigned_user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE todos ADD CONSTRAINT fk_todos_3 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE peer_messages ADD CONSTRAINT fk_peer_messages_0 FOREIGN KEY (workspace_id,goal_round_id) REFERENCES goal_rounds(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE peer_messages ADD CONSTRAINT fk_peer_messages_1 FOREIGN KEY (workspace_id,message_id) REFERENCES messages(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE peer_messages ADD CONSTRAINT fk_peer_messages_2 FOREIGN KEY (workspace_id,target_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE peer_messages ADD CONSTRAINT fk_peer_messages_3 FOREIGN KEY (workspace_id,sender_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE peer_messages ADD CONSTRAINT fk_peer_messages_4 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE handoffs ADD CONSTRAINT fk_handoffs_0 FOREIGN KEY (workspace_id,goal_round_id) REFERENCES goal_rounds(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE handoffs ADD CONSTRAINT fk_handoffs_1 FOREIGN KEY (workspace_id,target_group_id) REFERENCES groups(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE handoffs ADD CONSTRAINT fk_handoffs_2 FOREIGN KEY (workspace_id,target_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE handoffs ADD CONSTRAINT fk_handoffs_3 FOREIGN KEY (workspace_id,sender_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE handoffs ADD CONSTRAINT fk_handoffs_4 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE handoff_deliveries ADD CONSTRAINT fk_handoff_deliveries_0 FOREIGN KEY (workspace_id,message_id) REFERENCES messages(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE handoff_deliveries ADD CONSTRAINT fk_handoff_deliveries_1 FOREIGN KEY (workspace_id,recipient_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE handoff_deliveries ADD CONSTRAINT fk_handoff_deliveries_2 FOREIGN KEY (workspace_id,handoff_id) REFERENCES handoffs(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE handoff_deliveries ADD CONSTRAINT fk_handoff_deliveries_3 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE channels ADD CONSTRAINT fk_channels_0 FOREIGN KEY (workspace_id,conversation_id) REFERENCES conversations(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE channels ADD CONSTRAINT fk_channels_1 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE channel_posts ADD CONSTRAINT fk_channel_posts_0 FOREIGN KEY (workspace_id,conversation_id,message_id) REFERENCES messages(workspace_id,conversation_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE channel_posts ADD CONSTRAINT fk_channel_posts_1 FOREIGN KEY (workspace_id,conversation_id,channel_id) REFERENCES channels(workspace_id,conversation_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE channel_posts ADD CONSTRAINT fk_channel_posts_2 FOREIGN KEY (workspace_id,message_id) REFERENCES messages(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE channel_posts ADD CONSTRAINT fk_channel_posts_3 FOREIGN KEY (workspace_id,channel_id) REFERENCES channels(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE channel_posts ADD CONSTRAINT fk_channel_posts_4 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE channel_mentions ADD CONSTRAINT fk_channel_mentions_0 FOREIGN KEY (workspace_id,target_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE channel_mentions ADD CONSTRAINT fk_channel_mentions_1 FOREIGN KEY (workspace_id,post_id) REFERENCES channel_posts(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE channel_mentions ADD CONSTRAINT fk_channel_mentions_2 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE channel_digests ADD CONSTRAINT fk_channel_digests_0 FOREIGN KEY (workspace_id,channel_id,previous_digest_id) REFERENCES channel_digests(workspace_id,channel_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE channel_digests ADD CONSTRAINT fk_channel_digests_1 FOREIGN KEY (workspace_id,channel_id,through_post_id) REFERENCES channel_posts(workspace_id,channel_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE channel_digests ADD CONSTRAINT fk_channel_digests_2 FOREIGN KEY (workspace_id,execution_id) REFERENCES executions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE channel_digests ADD CONSTRAINT fk_channel_digests_3 FOREIGN KEY (workspace_id,previous_digest_id) REFERENCES channel_digests(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE channel_digests ADD CONSTRAINT fk_channel_digests_4 FOREIGN KEY (workspace_id,through_post_id) REFERENCES channel_posts(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE channel_digests ADD CONSTRAINT fk_channel_digests_5 FOREIGN KEY (workspace_id,channel_id) REFERENCES channels(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE channel_digests ADD CONSTRAINT fk_channel_digests_6 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE comments ADD CONSTRAINT fk_comments_0 FOREIGN KEY (workspace_id,message_id) REFERENCES messages(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE comments ADD CONSTRAINT fk_comments_1 FOREIGN KEY (workspace_id,report_id) REFERENCES reports(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE comments ADD CONSTRAINT fk_comments_2 FOREIGN KEY (workspace_id,artifact_id) REFERENCES artifacts(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE comments ADD CONSTRAINT fk_comments_3 FOREIGN KEY (workspace_id,goal_id) REFERENCES goals(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE comments ADD CONSTRAINT fk_comments_4 FOREIGN KEY (workspace_id,group_id) REFERENCES groups(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE comments ADD CONSTRAINT fk_comments_5 FOREIGN KEY (workspace_id,agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE comments ADD CONSTRAINT fk_comments_6 FOREIGN KEY (workspace_id,parent_comment_id) REFERENCES comments(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE comments ADD CONSTRAINT fk_comments_7 FOREIGN KEY (workspace_id,author_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE comments ADD CONSTRAINT fk_comments_8 FOREIGN KEY (author_user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE comments ADD CONSTRAINT fk_comments_9 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE trust_settings ADD CONSTRAINT fk_trust_settings_0 FOREIGN KEY (workspace_id,leader_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE trust_settings ADD CONSTRAINT fk_trust_settings_1 FOREIGN KEY (author_user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE trust_settings ADD CONSTRAINT fk_trust_settings_2 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE policies ADD CONSTRAINT fk_policies_0 FOREIGN KEY (author_user_id) REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE policies ADD CONSTRAINT fk_policies_1 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE git_requests ADD CONSTRAINT fk_git_requests_0 FOREIGN KEY (workspace_id,goal_round_id) REFERENCES goal_rounds(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE git_requests ADD CONSTRAINT fk_git_requests_1 FOREIGN KEY (workspace_id,administrator_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE git_requests ADD CONSTRAINT fk_git_requests_2 FOREIGN KEY (workspace_id,requester_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE git_requests ADD CONSTRAINT fk_git_requests_3 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE git_results ADD CONSTRAINT fk_git_results_0 FOREIGN KEY (workspace_id,author_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE git_results ADD CONSTRAINT fk_git_results_1 FOREIGN KEY (workspace_id,request_id) REFERENCES git_requests(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE git_results ADD CONSTRAINT fk_git_results_2 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE usage_observations ADD CONSTRAINT fk_usage_observations_0 FOREIGN KEY (workspace_id,message_id) REFERENCES messages(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE usage_observations ADD CONSTRAINT fk_usage_observations_1 FOREIGN KEY (workspace_id,execution_id) REFERENCES executions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE usage_observations ADD CONSTRAINT fk_usage_observations_2 FOREIGN KEY (workspace_id,agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE usage_observations ADD CONSTRAINT fk_usage_observations_3 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE import_batches ADD CONSTRAINT fk_import_batches_0 FOREIGN KEY (workspace_id,archive_artifact_version_id) REFERENCES artifact_versions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE import_batches ADD CONSTRAINT fk_import_batches_1 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE legacy_records ADD CONSTRAINT fk_legacy_records_0 FOREIGN KEY (workspace_id,raw_blob_id) REFERENCES blob_objects(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE legacy_records ADD CONSTRAINT fk_legacy_records_1 FOREIGN KEY (workspace_id,import_batch_id) REFERENCES import_batches(workspace_id,id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE legacy_records ADD CONSTRAINT fk_legacy_records_2 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE entity_tombstones ADD CONSTRAINT fk_entity_tombstones_0 FOREIGN KEY (workspace_id) REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED;

CREATE INDEX idx_agents_status ON agents (workspace_id, status);

CREATE INDEX idx_groups_leader ON groups (workspace_id, leader_agent_id);

CREATE INDEX idx_conversations_goal ON conversations (workspace_id, goal_id);

CREATE INDEX idx_messages_causal ON messages (workspace_id, conversation_id, reply_to_id, id);

CREATE INDEX idx_goals_owner_status ON goals (workspace_id, owner_agent_id, status);

CREATE INDEX idx_assignments_agent ON goal_assignments (workspace_id, agent_id, status);

CREATE INDEX idx_executions_agent_status ON executions (workspace_id, agent_id, status);

CREATE INDEX idx_executions_round ON executions (workspace_id, goal_round_id);

CREATE INDEX idx_reports_round ON reports (workspace_id, goal_round_id, created_at);

CREATE INDEX idx_artifact_versions_blob ON artifact_versions (workspace_id, blob_id);

CREATE INDEX idx_artifact_links_goal ON artifact_links (workspace_id, goal_round_id);

CREATE INDEX idx_artifact_links_message ON artifact_links (workspace_id, message_id);

CREATE INDEX idx_context_items_scope ON context_items (workspace_id, scope, goal_id, owner_agent_id);

CREATE INDEX idx_decisions_status ON decisions (workspace_id, status, requester_agent_id);

CREATE INDEX idx_todos_status ON todos (workspace_id, status, blocking);

CREATE INDEX idx_peer_messages_target ON peer_messages (workspace_id, target_agent_id, status);

CREATE INDEX idx_handoffs_target ON handoffs (workspace_id, target_agent_id, target_group_id, status);

CREATE INDEX idx_channel_mentions_target ON channel_mentions (workspace_id, target_agent_id);

CREATE INDEX idx_comments_goal ON comments (workspace_id, goal_id);

CREATE INDEX idx_git_requests_status ON git_requests (workspace_id, status);

CREATE INDEX idx_usage_execution ON usage_observations (workspace_id, execution_id, observed_at);

CREATE INDEX idx_usage_model_time ON usage_observations (workspace_id, model, observed_at);

CREATE INDEX idx_import_origin ON import_batches (workspace_id, legacy_origin_id);

CREATE INDEX idx_tombstones_state ON entity_tombstones (workspace_id, state, entity_type);

CREATE OR REPLACE FUNCTION guard_users_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'users: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_users_identity_revision ON users;

CREATE TRIGGER guard_users_identity_revision BEFORE UPDATE ON users FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision) EXECUTE FUNCTION guard_users_identity_revision_fn();

CREATE OR REPLACE FUNCTION guard_accounts_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'accounts: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_accounts_identity_revision ON accounts;

CREATE TRIGGER guard_accounts_identity_revision BEFORE UPDATE ON accounts FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision) EXECUTE FUNCTION guard_accounts_identity_revision_fn();

CREATE OR REPLACE FUNCTION guard_account_memberships_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'account_memberships: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_account_memberships_identity_revision ON account_memberships;

CREATE TRIGGER guard_account_memberships_identity_revision BEFORE UPDATE ON account_memberships FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision) EXECUTE FUNCTION guard_account_memberships_identity_revision_fn();

CREATE OR REPLACE FUNCTION guard_user_settings_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'user_settings: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_user_settings_identity_revision ON user_settings;

CREATE TRIGGER guard_user_settings_identity_revision BEFORE UPDATE ON user_settings FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision) EXECUTE FUNCTION guard_user_settings_identity_revision_fn();

CREATE OR REPLACE FUNCTION guard_workspaces_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'workspaces: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_workspaces_identity_revision ON workspaces;

CREATE TRIGGER guard_workspaces_identity_revision BEFORE UPDATE ON workspaces FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.account_id IS DISTINCT FROM OLD.account_id) EXECUTE FUNCTION guard_workspaces_identity_revision_fn();

CREATE OR REPLACE FUNCTION guard_workspace_memberships_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'workspace_memberships: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_workspace_memberships_identity_revision ON workspace_memberships;

CREATE TRIGGER guard_workspace_memberships_identity_revision BEFORE UPDATE ON workspace_memberships FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_workspace_memberships_identity_revision_fn();

CREATE OR REPLACE FUNCTION guard_agents_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'agents: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_agents_identity_revision ON agents;

CREATE TRIGGER guard_agents_identity_revision BEFORE UPDATE ON agents FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_agents_identity_revision_fn();

CREATE OR REPLACE FUNCTION guard_groups_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'groups: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_groups_identity_revision ON groups;

CREATE TRIGGER guard_groups_identity_revision BEFORE UPDATE ON groups FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_groups_identity_revision_fn();

CREATE OR REPLACE FUNCTION guard_group_memberships_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'group_memberships: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_group_memberships_identity_revision ON group_memberships;

CREATE TRIGGER guard_group_memberships_identity_revision BEFORE UPDATE ON group_memberships FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_group_memberships_identity_revision_fn();

CREATE OR REPLACE FUNCTION guard_blob_objects_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'blob_objects: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_blob_objects_identity_revision ON blob_objects;

CREATE TRIGGER guard_blob_objects_identity_revision BEFORE UPDATE ON blob_objects FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
  OR NEW.sha256 IS DISTINCT FROM OLD.sha256
  OR NEW.size_bytes IS DISTINCT FROM OLD.size_bytes) EXECUTE FUNCTION guard_blob_objects_identity_revision_fn();

CREATE OR REPLACE FUNCTION guard_conversations_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'conversations: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_conversations_identity_revision ON conversations;

CREATE TRIGGER guard_conversations_identity_revision BEFORE UPDATE ON conversations FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_conversations_identity_revision_fn();

CREATE OR REPLACE FUNCTION immutable_messages_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'messages is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_messages_update ON messages;

CREATE TRIGGER immutable_messages_update BEFORE UPDATE ON messages FOR EACH ROW EXECUTE FUNCTION immutable_messages_update_fn();

CREATE OR REPLACE FUNCTION immutable_message_positions_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'message_positions is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_message_positions_update ON message_positions;

CREATE TRIGGER immutable_message_positions_update BEFORE UPDATE ON message_positions FOR EACH ROW EXECUTE FUNCTION immutable_message_positions_update_fn();

CREATE OR REPLACE FUNCTION immutable_message_parts_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'message_parts is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_message_parts_update ON message_parts;

CREATE TRIGGER immutable_message_parts_update BEFORE UPDATE ON message_parts FOR EACH ROW EXECUTE FUNCTION immutable_message_parts_update_fn();

CREATE OR REPLACE FUNCTION immutable_tool_calls_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'tool_calls is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_tool_calls_update ON tool_calls;

CREATE TRIGGER immutable_tool_calls_update BEFORE UPDATE ON tool_calls FOR EACH ROW EXECUTE FUNCTION immutable_tool_calls_update_fn();

CREATE OR REPLACE FUNCTION immutable_tool_results_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'tool_results is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_tool_results_update ON tool_results;

CREATE TRIGGER immutable_tool_results_update BEFORE UPDATE ON tool_results FOR EACH ROW EXECUTE FUNCTION immutable_tool_results_update_fn();

CREATE OR REPLACE FUNCTION guard_goals_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'goals: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_goals_identity_revision ON goals;

CREATE TRIGGER guard_goals_identity_revision BEFORE UPDATE ON goals FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_goals_identity_revision_fn();

CREATE OR REPLACE FUNCTION guard_goal_dependencies_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'goal_dependencies: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_goal_dependencies_identity_revision ON goal_dependencies;

CREATE TRIGGER guard_goal_dependencies_identity_revision BEFORE UPDATE ON goal_dependencies FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_goal_dependencies_identity_revision_fn();

CREATE OR REPLACE FUNCTION immutable_goal_requirements_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'goal_requirements is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_goal_requirements_update ON goal_requirements;

CREATE TRIGGER immutable_goal_requirements_update BEFORE UPDATE ON goal_requirements FOR EACH ROW EXECUTE FUNCTION immutable_goal_requirements_update_fn();

CREATE OR REPLACE FUNCTION guard_goal_rounds_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'goal_rounds: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_goal_rounds_identity_revision ON goal_rounds;

CREATE TRIGGER guard_goal_rounds_identity_revision BEFORE UPDATE ON goal_rounds FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_goal_rounds_identity_revision_fn();

CREATE OR REPLACE FUNCTION guard_goal_assignments_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'goal_assignments: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_goal_assignments_identity_revision ON goal_assignments;

CREATE TRIGGER guard_goal_assignments_identity_revision BEFORE UPDATE ON goal_assignments FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_goal_assignments_identity_revision_fn();

CREATE OR REPLACE FUNCTION guard_executions_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'executions: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_executions_identity_revision ON executions;

CREATE TRIGGER guard_executions_identity_revision BEFORE UPDATE ON executions FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_executions_identity_revision_fn();

CREATE OR REPLACE FUNCTION immutable_goal_acceptances_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'goal_acceptances is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_goal_acceptances_update ON goal_acceptances;

CREATE TRIGGER immutable_goal_acceptances_update BEFORE UPDATE ON goal_acceptances FOR EACH ROW EXECUTE FUNCTION immutable_goal_acceptances_update_fn();

CREATE OR REPLACE FUNCTION immutable_reports_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'reports is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_reports_update ON reports;

CREATE TRIGGER immutable_reports_update BEFORE UPDATE ON reports FOR EACH ROW EXECUTE FUNCTION immutable_reports_update_fn();

CREATE OR REPLACE FUNCTION guard_artifacts_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'artifacts: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_artifacts_identity_revision ON artifacts;

CREATE TRIGGER guard_artifacts_identity_revision BEFORE UPDATE ON artifacts FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_artifacts_identity_revision_fn();

CREATE OR REPLACE FUNCTION immutable_artifact_versions_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'artifact_versions is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_artifact_versions_update ON artifact_versions;

CREATE TRIGGER immutable_artifact_versions_update BEFORE UPDATE ON artifact_versions FOR EACH ROW EXECUTE FUNCTION immutable_artifact_versions_update_fn();

CREATE OR REPLACE FUNCTION immutable_artifact_links_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'artifact_links is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_artifact_links_update ON artifact_links;

CREATE TRIGGER immutable_artifact_links_update BEFORE UPDATE ON artifact_links FOR EACH ROW EXECUTE FUNCTION immutable_artifact_links_update_fn();

CREATE OR REPLACE FUNCTION immutable_native_archives_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'native_archives is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_native_archives_update ON native_archives;

CREATE TRIGGER immutable_native_archives_update BEFORE UPDATE ON native_archives FOR EACH ROW EXECUTE FUNCTION immutable_native_archives_update_fn();

CREATE OR REPLACE FUNCTION guard_context_items_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'context_items: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_context_items_identity_revision ON context_items;

CREATE TRIGGER guard_context_items_identity_revision BEFORE UPDATE ON context_items FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_context_items_identity_revision_fn();

CREATE OR REPLACE FUNCTION immutable_context_versions_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'context_versions is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_context_versions_update ON context_versions;

CREATE TRIGGER immutable_context_versions_update BEFORE UPDATE ON context_versions FOR EACH ROW EXECUTE FUNCTION immutable_context_versions_update_fn();

CREATE OR REPLACE FUNCTION immutable_context_version_sources_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'context_version_sources is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_context_version_sources_update ON context_version_sources;

CREATE TRIGGER immutable_context_version_sources_update BEFORE UPDATE ON context_version_sources FOR EACH ROW EXECUTE FUNCTION immutable_context_version_sources_update_fn();

CREATE OR REPLACE FUNCTION immutable_context_manifests_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'context_manifests is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_context_manifests_update ON context_manifests;

CREATE TRIGGER immutable_context_manifests_update BEFORE UPDATE ON context_manifests FOR EACH ROW EXECUTE FUNCTION immutable_context_manifests_update_fn();

CREATE OR REPLACE FUNCTION immutable_context_manifest_entries_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'context_manifest_entries is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_context_manifest_entries_update ON context_manifest_entries;

CREATE TRIGGER immutable_context_manifest_entries_update BEFORE UPDATE ON context_manifest_entries FOR EACH ROW EXECUTE FUNCTION immutable_context_manifest_entries_update_fn();

CREATE OR REPLACE FUNCTION immutable_context_applications_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'context_applications is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_context_applications_update ON context_applications;

CREATE TRIGGER immutable_context_applications_update BEFORE UPDATE ON context_applications FOR EACH ROW EXECUTE FUNCTION immutable_context_applications_update_fn();

CREATE OR REPLACE FUNCTION guard_decisions_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'decisions: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_decisions_identity_revision ON decisions;

CREATE TRIGGER guard_decisions_identity_revision BEFORE UPDATE ON decisions FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_decisions_identity_revision_fn();

CREATE OR REPLACE FUNCTION immutable_decision_versions_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'decision_versions is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_decision_versions_update ON decision_versions;

CREATE TRIGGER immutable_decision_versions_update BEFORE UPDATE ON decision_versions FOR EACH ROW EXECUTE FUNCTION immutable_decision_versions_update_fn();

CREATE OR REPLACE FUNCTION immutable_decision_options_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'decision_options is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_decision_options_update ON decision_options;

CREATE TRIGGER immutable_decision_options_update BEFORE UPDATE ON decision_options FOR EACH ROW EXECUTE FUNCTION immutable_decision_options_update_fn();

CREATE OR REPLACE FUNCTION immutable_decision_answers_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'decision_answers is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_decision_answers_update ON decision_answers;

CREATE TRIGGER immutable_decision_answers_update BEFORE UPDATE ON decision_answers FOR EACH ROW EXECUTE FUNCTION immutable_decision_answers_update_fn();

CREATE OR REPLACE FUNCTION guard_todos_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'todos: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_todos_identity_revision ON todos;

CREATE TRIGGER guard_todos_identity_revision BEFORE UPDATE ON todos FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_todos_identity_revision_fn();

CREATE OR REPLACE FUNCTION guard_peer_messages_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'peer_messages: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_peer_messages_identity_revision ON peer_messages;

CREATE TRIGGER guard_peer_messages_identity_revision BEFORE UPDATE ON peer_messages FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_peer_messages_identity_revision_fn();

CREATE OR REPLACE FUNCTION guard_handoffs_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'handoffs: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_handoffs_identity_revision ON handoffs;

CREATE TRIGGER guard_handoffs_identity_revision BEFORE UPDATE ON handoffs FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_handoffs_identity_revision_fn();

CREATE OR REPLACE FUNCTION immutable_handoff_deliveries_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'handoff_deliveries is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_handoff_deliveries_update ON handoff_deliveries;

CREATE TRIGGER immutable_handoff_deliveries_update BEFORE UPDATE ON handoff_deliveries FOR EACH ROW EXECUTE FUNCTION immutable_handoff_deliveries_update_fn();

CREATE OR REPLACE FUNCTION guard_channels_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'channels: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_channels_identity_revision ON channels;

CREATE TRIGGER guard_channels_identity_revision BEFORE UPDATE ON channels FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_channels_identity_revision_fn();

CREATE OR REPLACE FUNCTION immutable_channel_posts_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'channel_posts is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_channel_posts_update ON channel_posts;

CREATE TRIGGER immutable_channel_posts_update BEFORE UPDATE ON channel_posts FOR EACH ROW EXECUTE FUNCTION immutable_channel_posts_update_fn();

CREATE OR REPLACE FUNCTION immutable_channel_mentions_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'channel_mentions is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_channel_mentions_update ON channel_mentions;

CREATE TRIGGER immutable_channel_mentions_update BEFORE UPDATE ON channel_mentions FOR EACH ROW EXECUTE FUNCTION immutable_channel_mentions_update_fn();

CREATE OR REPLACE FUNCTION immutable_channel_digests_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'channel_digests is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_channel_digests_update ON channel_digests;

CREATE TRIGGER immutable_channel_digests_update BEFORE UPDATE ON channel_digests FOR EACH ROW EXECUTE FUNCTION immutable_channel_digests_update_fn();

CREATE OR REPLACE FUNCTION guard_comments_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'comments: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_comments_identity_revision ON comments;

CREATE TRIGGER guard_comments_identity_revision BEFORE UPDATE ON comments FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_comments_identity_revision_fn();

CREATE OR REPLACE FUNCTION guard_trust_settings_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'trust_settings: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_trust_settings_identity_revision ON trust_settings;

CREATE TRIGGER guard_trust_settings_identity_revision BEFORE UPDATE ON trust_settings FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_trust_settings_identity_revision_fn();

CREATE OR REPLACE FUNCTION guard_policies_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'policies: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_policies_identity_revision ON policies;

CREATE TRIGGER guard_policies_identity_revision BEFORE UPDATE ON policies FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_policies_identity_revision_fn();

CREATE OR REPLACE FUNCTION guard_git_requests_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'git_requests: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_git_requests_identity_revision ON git_requests;

CREATE TRIGGER guard_git_requests_identity_revision BEFORE UPDATE ON git_requests FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_git_requests_identity_revision_fn();

CREATE OR REPLACE FUNCTION immutable_git_results_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'git_results is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_git_results_update ON git_results;

CREATE TRIGGER immutable_git_results_update BEFORE UPDATE ON git_results FOR EACH ROW EXECUTE FUNCTION immutable_git_results_update_fn();

CREATE OR REPLACE FUNCTION immutable_usage_observations_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'usage_observations is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_usage_observations_update ON usage_observations;

CREATE TRIGGER immutable_usage_observations_update BEFORE UPDATE ON usage_observations FOR EACH ROW EXECUTE FUNCTION immutable_usage_observations_update_fn();

CREATE OR REPLACE FUNCTION guard_import_batches_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'import_batches: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_import_batches_identity_revision ON import_batches;

CREATE TRIGGER guard_import_batches_identity_revision BEFORE UPDATE ON import_batches FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_import_batches_identity_revision_fn();

CREATE OR REPLACE FUNCTION immutable_legacy_records_update_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'legacy_records is immutable; append a replacement or tombstone';
END;
$$;

DROP TRIGGER IF EXISTS immutable_legacy_records_update ON legacy_records;

CREATE TRIGGER immutable_legacy_records_update BEFORE UPDATE ON legacy_records FOR EACH ROW EXECUTE FUNCTION immutable_legacy_records_update_fn();

CREATE OR REPLACE FUNCTION guard_entity_tombstones_identity_revision_fn() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'entity_tombstones: identity is fixed and revision must advance';
END;
$$;

DROP TRIGGER IF EXISTS guard_entity_tombstones_identity_revision ON entity_tombstones;

CREATE TRIGGER guard_entity_tombstones_identity_revision BEFORE UPDATE ON entity_tombstones FOR EACH ROW WHEN (NEW.id IS DISTINCT FROM OLD.id
  OR NEW.created_at IS DISTINCT FROM OLD.created_at
  OR NEW.revision<=OLD.revision
  OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id) EXECUTE FUNCTION guard_entity_tombstones_identity_revision_fn();

COMMIT;
