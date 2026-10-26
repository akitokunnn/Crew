-- Storage v2 proposal: machine-local SQLite extension, applied AFTER sqlite.sql.
-- These tables live in the SAME database as domain rows so a Repository
-- transaction atomically records pending proposals + durable outbox; domain
-- rows contain ACCEPTED state only and are materialized from accepted commits.
-- NONE of local_* is part of a portable workspace export. This is design DDL;
-- it is not a migration for the running Crew database.
PRAGMA foreign_keys = ON;

CREATE TABLE local_schema_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    min_reader_version INTEGER NOT NULL CHECK (min_reader_version >= 1),
    min_writer_version INTEGER NOT NULL CHECK (min_writer_version >= min_reader_version),
    profile_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE local_schema_migrations (
    id TEXT PRIMARY KEY,
    from_version INTEGER NOT NULL,
    to_version INTEGER NOT NULL CHECK (to_version > from_version),
    migration_sha256 TEXT NOT NULL CHECK (length(migration_sha256) = 64 AND migration_sha256 NOT GLOB '*[^0-9a-f]*'),
    applied_at TEXT NOT NULL
);

-- Device enrollment is attribution, never authentication or authorization.
CREATE TABLE local_devices (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    platform TEXT NOT NULL,
    next_client_sequence INTEGER NOT NULL DEFAULT 1 CHECK (next_client_sequence >= 1),
    created_at TEXT NOT NULL,
    last_seen_at TEXT,
    retired_at TEXT
);
CREATE TABLE local_workspace_locations (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    device_id TEXT NOT NULL REFERENCES local_devices(id),
    root_path TEXT NOT NULL,
    path_key TEXT NOT NULL,
    git_common_dir TEXT,
    availability TEXT NOT NULL CHECK (availability IN ('unchecked','available','missing','unmounted','permission_denied')),
    verified_at TEXT,
    retired_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (device_id, workspace_id, path_key),
    UNIQUE (workspace_id, id)
);
CREATE UNIQUE INDEX local_one_active_workspace_at_path ON local_workspace_locations(device_id, path_key)
    WHERE retired_at IS NULL;
CREATE TABLE local_secret_refs (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES local_devices(id),
    workspace_id TEXT REFERENCES workspaces(id),
    provider TEXT NOT NULL CHECK (provider IN ('credential_manager','keychain','libsecret','external_vault')),
    opaque_key TEXT NOT NULL,
    purpose TEXT NOT NULL,
    created_at TEXT NOT NULL,
    rotated_at TEXT,
    UNIQUE (device_id, provider, opaque_key)
    -- Values, refresh tokens, API keys and signed upload URLs NEVER go here.
);
CREATE TABLE local_native_bindings (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    device_id TEXT NOT NULL REFERENCES local_devices(id),
    agent_id TEXT,
    execution_id TEXT,
    archive_id TEXT,
    runtime TEXT NOT NULL,
    native_id TEXT NOT NULL,
    native_locator TEXT,
    tool_version TEXT,
    account_subject_hash TEXT,
    state TEXT NOT NULL CHECK (state IN ('unverified','verified','incompatible','missing','retired')),
    revalidated_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (agent_id IS NOT NULL OR execution_id IS NOT NULL),
    UNIQUE (workspace_id, device_id, runtime, native_id),
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, agent_id) REFERENCES agents(workspace_id, id),
    FOREIGN KEY (workspace_id, execution_id) REFERENCES executions(workspace_id, id),
    FOREIGN KEY (workspace_id, archive_id) REFERENCES native_archives(workspace_id, id)
);
CREATE TABLE local_drafts (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    device_id TEXT NOT NULL REFERENCES local_devices(id),
    conversation_id TEXT,
    goal_id TEXT,
    kind TEXT NOT NULL,
    body_text TEXT NOT NULL,
    base_revision INTEGER CHECK (base_revision IS NULL OR base_revision >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, conversation_id) REFERENCES conversations(workspace_id, id),
    FOREIGN KEY (workspace_id, goal_id) REFERENCES goals(workspace_id, id)
);

-- Verified lease proofs are machine state, not portable execution authority.
-- Deadlines use the process monotonic clock and are invalid after restart.
CREATE TABLE local_run_leases (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    device_id TEXT NOT NULL REFERENCES local_devices(id),
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('agent','goal')),
    agent_id TEXT,
    goal_id TEXT,
    cloud_lease_id TEXT NOT NULL,
    authority_epoch INTEGER NOT NULL CHECK (authority_epoch >= 1),
    fencing_token INTEGER NOT NULL CHECK (fencing_token >= 1),
    server_expires_at TEXT NOT NULL,
    clock_boot_id TEXT NOT NULL,
    monotonic_deadline_ms INTEGER NOT NULL CHECK (monotonic_deadline_ms >= 0),
    proof_json TEXT NOT NULL CHECK (json_valid(proof_json)),
    state TEXT NOT NULL CHECK (state IN ('valid','expired','revoked','unknown')),
    verified_at TEXT NOT NULL,
    CHECK ((scope_kind = 'agent' AND agent_id IS NOT NULL AND goal_id IS NULL)
        OR (scope_kind = 'goal' AND goal_id IS NOT NULL AND agent_id IS NULL)),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, cloud_lease_id),
    FOREIGN KEY (workspace_id, agent_id) REFERENCES agents(workspace_id, id),
    FOREIGN KEY (workspace_id, goal_id) REFERENCES goals(workspace_id, id)
);
CREATE TABLE local_runtime_sessions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    device_id TEXT NOT NULL REFERENCES local_devices(id),
    agent_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    native_binding_id TEXT,
    agent_lease_id TEXT NOT NULL,
    goal_lease_id TEXT,
    process_id INTEGER,
    process_birth_token TEXT,
    process_group_token TEXT,
    phase TEXT NOT NULL CHECK (phase IN ('starting','running','stopping','stopped','lost','unknown')),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    last_heartbeat_at TEXT,
    error_text TEXT,
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, agent_id) REFERENCES agents(workspace_id, id),
    FOREIGN KEY (workspace_id, execution_id) REFERENCES executions(workspace_id, id),
    FOREIGN KEY (workspace_id, native_binding_id) REFERENCES local_native_bindings(workspace_id, id),
    FOREIGN KEY (workspace_id, agent_lease_id) REFERENCES local_run_leases(workspace_id, id),
    FOREIGN KEY (workspace_id, goal_lease_id) REFERENCES local_run_leases(workspace_id, id)
);
CREATE UNIQUE INDEX local_one_live_runtime_per_agent ON local_runtime_sessions(workspace_id, agent_id)
    WHERE phase IN ('starting','running','stopping','unknown');

-- Device/app/file ownership is separate from cloud agent/goal execution rights.
CREATE TABLE local_resources (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    device_id TEXT NOT NULL REFERENCES local_devices(id),
    resource_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('device','directory','application','other')),
    native_locator TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, device_id, resource_key),
    UNIQUE (workspace_id, id)
);
CREATE TABLE local_resource_leases (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    resource_id TEXT NOT NULL,
    runtime_session_id TEXT,
    holder_agent_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('held','released','expired','unknown')),
    acquired_at TEXT NOT NULL,
    expires_at TEXT,
    released_at TEXT,
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, resource_id) REFERENCES local_resources(workspace_id, id),
    FOREIGN KEY (workspace_id, runtime_session_id) REFERENCES local_runtime_sessions(workspace_id, id),
    FOREIGN KEY (workspace_id, holder_agent_id) REFERENCES agents(workspace_id, id)
);
CREATE UNIQUE INDEX local_one_resource_holder ON local_resource_leases(workspace_id, resource_id)
    WHERE state IN ('held','unknown');
CREATE TABLE local_file_claims (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    device_id TEXT NOT NULL REFERENCES local_devices(id),
    agent_id TEXT NOT NULL,
    runtime_session_id TEXT,
    path_pattern TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('held','released','expired')),
    acquired_at TEXT NOT NULL,
    released_at TEXT,
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, agent_id) REFERENCES agents(workspace_id, id),
    FOREIGN KEY (workspace_id, runtime_session_id) REFERENCES local_runtime_sessions(workspace_id, id)
);

CREATE TABLE local_outbox (
    operation_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    device_id TEXT NOT NULL REFERENCES local_devices(id),
    client_sequence INTEGER NOT NULL CHECK (client_sequence >= 1),
    authority_epoch INTEGER NOT NULL CHECK (authority_epoch >= 1),
    observed_deletion_epoch INTEGER NOT NULL CHECK (observed_deletion_epoch >= 0),
    request_json TEXT NOT NULL CHECK (json_valid(request_json)),
    request_sha256 TEXT NOT NULL CHECK (length(request_sha256) = 64 AND request_sha256 NOT GLOB '*[^0-9a-f]*'),
    state TEXT NOT NULL CHECK (state IN ('queued','waiting_blobs','sending','accepted','conflicted','rejected','cancelled')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    accepted_server_seq INTEGER CHECK (accepted_server_seq IS NULL OR accepted_server_seq >= 1),
    response_json TEXT CHECK (response_json IS NULL OR json_valid(response_json)),
    error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (device_id, client_sequence),
    UNIQUE (workspace_id, operation_id),
    CHECK (state <> 'accepted' OR accepted_server_seq IS NOT NULL)
);
CREATE INDEX local_outbox_pending ON local_outbox(workspace_id, device_id, state, client_sequence);
CREATE TABLE local_outbox_dependencies (
    workspace_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    depends_on_operation_id TEXT NOT NULL,
    PRIMARY KEY (workspace_id, operation_id, depends_on_operation_id),
    CHECK (operation_id <> depends_on_operation_id),
    FOREIGN KEY (workspace_id, operation_id) REFERENCES local_outbox(workspace_id, operation_id),
    FOREIGN KEY (workspace_id, depends_on_operation_id) REFERENCES local_outbox(workspace_id, operation_id)
);
CREATE TABLE local_outbox_attempts (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result TEXT CHECK (result IN ('accepted','conflicted','rejected','transport_unknown')),
    http_status INTEGER,
    error_text TEXT,
    UNIQUE (workspace_id, operation_id, attempt_number),
    FOREIGN KEY (workspace_id, operation_id) REFERENCES local_outbox(workspace_id, operation_id)
);

-- Optional rebase/checkpoint cache of the accepted baseline; DOMAIN rows remain
-- its authority. Optimistic UI is an overlay of local_pending_mutations, never
-- a fabricated accepted revision in a domain row.
CREATE TABLE local_entity_sync_state (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    entity_kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    accepted_revision INTEGER NOT NULL CHECK (accepted_revision >= 0),
    accepted_json TEXT CHECK (accepted_json IS NULL OR json_valid(accepted_json)),
    accepted_deleted INTEGER NOT NULL CHECK (accepted_deleted IN (0,1)),
    local_generation INTEGER NOT NULL DEFAULT 0 CHECK (local_generation >= 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, entity_kind, entity_id)
);
CREATE TABLE local_pending_mutations (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    entity_kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    base_revision INTEGER NOT NULL CHECK (base_revision >= 0),
    change_kind TEXT NOT NULL CHECK (change_kind IN ('put','append','delete','restore')),
    state TEXT NOT NULL CHECK (state IN ('pending','accepted_waiting_pull','conflicted','rejected','materialized','discarded')),
    before_json TEXT CHECK (before_json IS NULL OR json_valid(before_json)),
    proposed_json TEXT CHECK (proposed_json IS NULL OR json_valid(proposed_json)),
    UNIQUE (workspace_id, operation_id, ordinal),
    FOREIGN KEY (workspace_id, operation_id) REFERENCES local_outbox(workspace_id, operation_id)
);
CREATE TABLE local_sync_cursors (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    device_id TEXT NOT NULL REFERENCES local_devices(id),
    authority_epoch INTEGER NOT NULL CHECK (authority_epoch >= 1),
    applied_through_seq INTEGER NOT NULL DEFAULT 0 CHECK (applied_through_seq >= 0),
    snapshot_generation INTEGER NOT NULL CHECK (snapshot_generation >= 1),
    deletion_epoch INTEGER NOT NULL DEFAULT 0 CHECK (deletion_epoch >= 0),
    membership_revision INTEGER NOT NULL DEFAULT 0 CHECK (membership_revision >= 0),
    access_state TEXT NOT NULL CHECK (access_state IN ('healthy','quarantined','revoked','needs_snapshot')),
    last_checked_at TEXT,
    PRIMARY KEY (workspace_id, device_id)
);
CREATE TABLE local_sync_inbox (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    authority_epoch INTEGER NOT NULL CHECK (authority_epoch >= 1),
    server_seq INTEGER NOT NULL CHECK (server_seq >= 1),
    deletion_epoch INTEGER NOT NULL CHECK (deletion_epoch >= 0),
    commit_id TEXT NOT NULL,
    commit_sha256 TEXT NOT NULL CHECK (length(commit_sha256) = 64 AND commit_sha256 NOT GLOB '*[^0-9a-f]*'),
    change_count INTEGER NOT NULL CHECK (change_count >= 1),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    received_at TEXT NOT NULL,
    applied_at TEXT,
    UNIQUE (workspace_id, authority_epoch, server_seq),
    UNIQUE (workspace_id, commit_id)
);
CREATE TABLE local_conflicts (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    entity_kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    base_revision INTEGER NOT NULL CHECK (base_revision >= 0),
    server_revision INTEGER NOT NULL CHECK (server_revision >= 0),
    base_json TEXT CHECK (base_json IS NULL OR json_valid(base_json)),
    local_json TEXT CHECK (local_json IS NULL OR json_valid(local_json)),
    server_json TEXT CHECK (server_json IS NULL OR json_valid(server_json)),
    state TEXT NOT NULL CHECK (state IN ('unresolved','resolved','discarded')),
    resolution_operation_id TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, operation_id) REFERENCES local_outbox(workspace_id, operation_id),
    FOREIGN KEY (workspace_id, resolution_operation_id) REFERENCES local_outbox(workspace_id, operation_id)
);

CREATE TABLE local_blob_cache (
    workspace_id TEXT NOT NULL,
    blob_id TEXT NOT NULL,
    relative_cache_path TEXT NOT NULL,
    verified_sha256 TEXT NOT NULL CHECK (length(verified_sha256) = 64 AND verified_sha256 NOT GLOB '*[^0-9a-f]*'),
    verified_byte_size INTEGER NOT NULL CHECK (verified_byte_size >= 0),
    verified_at TEXT NOT NULL,
    last_accessed_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, blob_id),
    FOREIGN KEY (workspace_id, blob_id) REFERENCES blob_objects(workspace_id, id)
);
CREATE TABLE local_blob_pins (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    blob_id TEXT NOT NULL,
    owner_kind TEXT NOT NULL CHECK (owner_kind IN ('snapshot','export','upload','download')),
    owner_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    released_at TEXT,
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, blob_id) REFERENCES blob_objects(workspace_id, id)
);
CREATE UNIQUE INDEX local_one_active_blob_pin ON local_blob_pins(workspace_id, blob_id, owner_kind, owner_id)
    WHERE released_at IS NULL;
CREATE TABLE local_blob_transfers (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    blob_id TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('upload','download')),
    state TEXT NOT NULL CHECK (state IN ('queued','transferring','verifying','complete','failed','cancelled')),
    cloud_upload_id TEXT,
    temporary_path TEXT,
    completed_bytes INTEGER NOT NULL DEFAULT 0 CHECK (completed_bytes >= 0),
    resume_json TEXT CHECK (resume_json IS NULL OR json_valid(resume_json)),
    error_text TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (workspace_id, id),
    FOREIGN KEY (workspace_id, blob_id) REFERENCES blob_objects(workspace_id, id)
);
CREATE TABLE local_view_cursors (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    device_id TEXT NOT NULL REFERENCES local_devices(id),
    conversation_id TEXT,
    channel_id TEXT,
    anchor_entity_id TEXT,
    accepted_sequence INTEGER CHECK (accepted_sequence IS NULL OR accepted_sequence >= 1),
    view_json TEXT NOT NULL CHECK (json_valid(view_json)),
    updated_at TEXT NOT NULL,
    CHECK ((conversation_id IS NOT NULL) <> (channel_id IS NOT NULL)),
    FOREIGN KEY (workspace_id, conversation_id) REFERENCES conversations(workspace_id, id),
    FOREIGN KEY (workspace_id, channel_id) REFERENCES channels(workspace_id, id)
);
CREATE TABLE local_migrations (
    id TEXT PRIMARY KEY,
    workspace_id TEXT REFERENCES workspaces(id),
    import_batch_id TEXT,
    source_path TEXT NOT NULL,
    staging_path TEXT,
    source_snapshot_sha256 TEXT,
    phase TEXT NOT NULL CHECK (phase IN ('discovered','copying','validating','ready','activated','failed','abandoned')),
    validation_json TEXT CHECK (validation_json IS NULL OR json_valid(validation_json)),
    error_text TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (import_batch_id IS NULL OR workspace_id IS NOT NULL),
    FOREIGN KEY (workspace_id, import_batch_id) REFERENCES import_batches(workspace_id, id)
);
CREATE TABLE local_export_jobs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    destination_path TEXT NOT NULL,
    completeness TEXT NOT NULL CHECK (completeness IN ('complete','metadata_only')),
    source_state TEXT NOT NULL CHECK (source_state IN ('accepted','working_copy')),
    authority_epoch INTEGER NOT NULL CHECK (authority_epoch >= 1),
    through_seq INTEGER NOT NULL CHECK (through_seq >= 0),
    deletion_epoch INTEGER NOT NULL CHECK (deletion_epoch >= 0),
    snapshot_id TEXT,
    state TEXT NOT NULL CHECK (state IN ('preparing','pinning','copying','verifying','complete','failed','cancelled')),
    manifest_sha256 TEXT,
    error_text TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
