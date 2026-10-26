-- Storage v2 proposal: PostgreSQL coordination extension. Apply AFTER the
-- PostgreSQL rendering of sqlite.sql. No domain entities or second ACL live
-- here: users/accounts/account_memberships/workspace_memberships are domain.
-- UUID identifiers use canonical lowercase UUID TEXT, matching portable data.
-- Server-only tables: NEVER export these as workspace content or credentials.

CREATE TABLE cloud_schema_metadata (
    singleton SMALLINT PRIMARY KEY CHECK (singleton = 1),
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    protocol_version INTEGER NOT NULL CHECK (protocol_version >= 1),
    min_reader_version INTEGER NOT NULL CHECK (min_reader_version >= 1),
    min_writer_version INTEGER NOT NULL CHECK (min_writer_version >= min_reader_version),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp()
);
CREATE TABLE cloud_auth_identities (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    verified_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    UNIQUE (issuer, subject)
);
CREATE TABLE cloud_account_devices (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    display_name TEXT NOT NULL,
    public_key_fingerprint TEXT,
    enrolled_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    last_seen_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    UNIQUE (account_id, id),
    UNIQUE (account_id, user_id, id),
    FOREIGN KEY (account_id, user_id) REFERENCES account_memberships(account_id, user_id)
);
CREATE TABLE cloud_auth_sessions (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    device_id TEXT,
    token_sha256 TEXT NOT NULL UNIQUE CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
    auth_revision BIGINT NOT NULL CHECK (auth_revision >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    CHECK (expires_at > created_at),
    FOREIGN KEY (account_id, user_id) REFERENCES account_memberships(account_id, user_id),
    FOREIGN KEY (account_id, user_id, device_id) REFERENCES cloud_account_devices(account_id, user_id, id)
    -- Token plaintext/refresh credentials belong in the auth provider/vault.
);

-- Lock this row FOR UPDATE before every accepted workspace transaction.
-- committed_through_seq is incremented transactionally, not via a SEQUENCE.
CREATE TABLE cloud_workspace_sequencers (
    workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 1),
    committed_through_seq BIGINT NOT NULL DEFAULT 0 CHECK (committed_through_seq >= 0),
    retained_after_seq BIGINT NOT NULL DEFAULT 0 CHECK (retained_after_seq >= 0),
    snapshot_generation BIGINT NOT NULL DEFAULT 1 CHECK (snapshot_generation >= 1),
    deletion_epoch BIGINT NOT NULL DEFAULT 0 CHECK (deletion_epoch >= 0),
    state TEXT NOT NULL CHECK (state IN ('active','deleting','deleted')),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    CHECK (retained_after_seq <= committed_through_seq)
);
CREATE TABLE cloud_operations (
    operation_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    actor_user_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 1),
    observed_deletion_epoch BIGINT NOT NULL CHECK (observed_deletion_epoch >= 0),
    request_sha256 TEXT NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
    request_json JSONB NOT NULL CHECK (jsonb_typeof(request_json) = 'object'),
    status TEXT NOT NULL CHECK (status IN ('accepted','conflicted','rejected')),
    server_seq BIGINT CHECK (server_seq IS NULL OR server_seq >= 1),
    response_json JSONB NOT NULL CHECK (jsonb_typeof(response_json) = 'object'),
    error_code TEXT,
    received_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (workspace_id, operation_id),
    UNIQUE (workspace_id, authority_epoch, server_seq),
    FOREIGN KEY (account_id, workspace_id) REFERENCES workspaces(account_id, id),
    FOREIGN KEY (account_id, actor_user_id, device_id) REFERENCES cloud_account_devices(account_id, user_id, id),
    CHECK ((status = 'accepted' AND server_seq IS NOT NULL AND error_code IS NULL)
        OR (status <> 'accepted' AND server_seq IS NULL AND error_code IS NOT NULL))
);
CREATE TABLE cloud_commits (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 1),
    server_seq BIGINT NOT NULL CHECK (server_seq >= 1),
    deletion_epoch BIGINT NOT NULL CHECK (deletion_epoch >= 0),
    operation_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    change_count INTEGER NOT NULL CHECK (change_count >= 1),
    commit_sha256 TEXT NOT NULL CHECK (commit_sha256 ~ '^[0-9a-f]{64}$'),
    accepted_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (workspace_id, authority_epoch, server_seq),
    UNIQUE (workspace_id, id),
    UNIQUE (workspace_id, operation_id),
    UNIQUE (workspace_id, operation_id, authority_epoch, server_seq),
    FOREIGN KEY (workspace_id, operation_id) REFERENCES cloud_operations(workspace_id, operation_id)
        DEFERRABLE INITIALLY DEFERRED
);
ALTER TABLE cloud_operations ADD CONSTRAINT cloud_operation_commit_fk
    FOREIGN KEY (workspace_id, operation_id, authority_epoch, server_seq)
    REFERENCES cloud_commits(workspace_id, operation_id, authority_epoch, server_seq)
    DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE cloud_change_items (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    authority_epoch BIGINT NOT NULL,
    server_seq BIGINT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    entity_kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    change_kind TEXT NOT NULL CHECK (change_kind IN ('put','append','delete','restore')),
    base_revision BIGINT NOT NULL CHECK (base_revision >= 0),
    accepted_revision BIGINT NOT NULL CHECK (accepted_revision >= 1),
    row_json JSONB,
    row_sha256 TEXT NOT NULL CHECK (row_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (workspace_id, id),
    UNIQUE (workspace_id, authority_epoch, server_seq, ordinal),
    FOREIGN KEY (workspace_id, authority_epoch, server_seq)
        REFERENCES cloud_commits(workspace_id, authority_epoch, server_seq),
    CHECK ((change_kind = 'delete' AND row_json IS NULL)
        OR (change_kind <> 'delete' AND row_json IS NOT NULL AND jsonb_typeof(row_json) = 'object'))
    -- entity_kind is a SERVER ALLOWLIST key; never an arbitrary SQL identifier.
);
CREATE TABLE cloud_device_cursors (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    account_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 1),
    applied_through_seq BIGINT NOT NULL CHECK (applied_through_seq >= 0),
    snapshot_generation BIGINT NOT NULL CHECK (snapshot_generation >= 1),
    deletion_epoch BIGINT NOT NULL CHECK (deletion_epoch >= 0),
    membership_revision BIGINT NOT NULL CHECK (membership_revision >= 1),
    state TEXT NOT NULL CHECK (state IN ('active','expired','revoked','needs_snapshot')),
    acknowledged_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (workspace_id, device_id),
    FOREIGN KEY (account_id, workspace_id) REFERENCES workspaces(account_id, id),
    FOREIGN KEY (account_id, device_id) REFERENCES cloud_account_devices(account_id, id)
);

-- Server verifies completed bytes, length AND SHA-256 before state=verified;
-- an object-store ETag or client-supplied checksum is not sufficient proof.
CREATE TABLE cloud_blob_uploads (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    actor_user_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    blob_id TEXT NOT NULL,
    expected_sha256 TEXT NOT NULL CHECK (expected_sha256 ~ '^[0-9a-f]{64}$'),
    expected_byte_size BIGINT NOT NULL CHECK (expected_byte_size >= 0),
    staging_object_key TEXT NOT NULL UNIQUE,
    final_object_key TEXT NOT NULL,
    provider_upload_id TEXT,
    state TEXT NOT NULL CHECK (state IN ('initiated','uploaded','verifying','verified','failed','expired','cancelled')),
    verified_sha256 TEXT CHECK (verified_sha256 IS NULL OR verified_sha256 ~ '^[0-9a-f]{64}$'),
    verified_byte_size BIGINT CHECK (verified_byte_size IS NULL OR verified_byte_size >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    verified_at TIMESTAMPTZ,
    error_code TEXT,
    PRIMARY KEY (workspace_id, id),
    FOREIGN KEY (workspace_id, blob_id) REFERENCES blob_objects(workspace_id, id),
    FOREIGN KEY (account_id, workspace_id) REFERENCES workspaces(account_id, id),
    FOREIGN KEY (account_id, actor_user_id, device_id) REFERENCES cloud_account_devices(account_id, user_id, id),
    CHECK (final_object_key = workspace_id || '/' || expected_sha256),
    CHECK (state <> 'verified' OR (verified_at IS NOT NULL AND verified_sha256 IS NOT NULL AND verified_byte_size IS NOT NULL
        AND verified_sha256 = expected_sha256 AND verified_byte_size = expected_byte_size))
);
CREATE INDEX cloud_blob_uploads_pending ON cloud_blob_uploads(workspace_id, state, expires_at);
CREATE TABLE cloud_blob_pins (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    blob_id TEXT NOT NULL,
    owner_kind TEXT NOT NULL CHECK (owner_kind IN ('snapshot','export','backup','upload')),
    owner_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (workspace_id, id),
    UNIQUE (workspace_id, blob_id, owner_kind, owner_id),
    FOREIGN KEY (workspace_id, blob_id) REFERENCES blob_objects(workspace_id, id)
);
CREATE TABLE cloud_snapshots (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 1),
    through_seq BIGINT NOT NULL CHECK (through_seq >= 0),
    snapshot_generation BIGINT NOT NULL CHECK (snapshot_generation >= 1),
    deletion_epoch BIGINT NOT NULL CHECK (deletion_epoch >= 0),
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    min_reader_version INTEGER NOT NULL CHECK (min_reader_version >= 1),
    min_writer_version INTEGER NOT NULL CHECK (min_writer_version >= min_reader_version),
    snapshot_blob_id TEXT NOT NULL,
    manifest_blob_id TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    state TEXT NOT NULL CHECK (state IN ('building','ready','invalid','expired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (workspace_id, id),
    FOREIGN KEY (workspace_id, snapshot_blob_id) REFERENCES blob_objects(workspace_id, id),
    FOREIGN KEY (workspace_id, manifest_blob_id) REFERENCES blob_objects(workspace_id, id)
);
CREATE INDEX cloud_snapshots_ready ON cloud_snapshots(workspace_id, authority_epoch, through_seq DESC)
    WHERE state = 'ready';

-- Separate slot heads prevent two devices from owning one agent or goal.
-- A goal lease can be shared by that device's parallel agent executions.
CREATE TABLE cloud_run_fences (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('agent','goal')),
    resource_id TEXT NOT NULL,
    last_fencing_token BIGINT NOT NULL DEFAULT 0 CHECK (last_fencing_token >= 0),
    current_lease_id TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (workspace_id, scope_kind, resource_id)
);
CREATE TABLE cloud_run_leases (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    scope_kind TEXT NOT NULL CHECK (scope_kind IN ('agent','goal')),
    resource_id TEXT NOT NULL,
    agent_id TEXT,
    goal_id TEXT,
    holder_user_id TEXT NOT NULL,
    holder_device_id TEXT NOT NULL,
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 1),
    fencing_token BIGINT NOT NULL CHECK (fencing_token >= 1),
    lease_revision BIGINT NOT NULL CHECK (lease_revision >= 1),
    issued_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    revoke_reason TEXT,
    PRIMARY KEY (workspace_id, id),
    UNIQUE (workspace_id, scope_kind, resource_id, fencing_token),
    UNIQUE (workspace_id, scope_kind, resource_id, id),
    UNIQUE (workspace_id, id, holder_device_id),
    FOREIGN KEY (workspace_id, scope_kind, resource_id) REFERENCES cloud_run_fences(workspace_id, scope_kind, resource_id),
    FOREIGN KEY (account_id, workspace_id) REFERENCES workspaces(account_id, id),
    FOREIGN KEY (account_id, holder_user_id, holder_device_id) REFERENCES cloud_account_devices(account_id, user_id, id),
    FOREIGN KEY (workspace_id, agent_id) REFERENCES agents(workspace_id, id),
    FOREIGN KEY (workspace_id, goal_id) REFERENCES goals(workspace_id, id),
    CHECK (expires_at > issued_at),
    CHECK ((scope_kind = 'agent' AND agent_id IS NOT NULL AND agent_id = resource_id AND goal_id IS NULL)
        OR (scope_kind = 'goal' AND goal_id IS NOT NULL AND goal_id = resource_id AND agent_id IS NULL))
);
ALTER TABLE cloud_run_fences ADD CONSTRAINT cloud_run_fences_current_lease_fk
    FOREIGN KEY (workspace_id, scope_kind, resource_id, current_lease_id)
    REFERENCES cloud_run_leases(workspace_id, scope_kind, resource_id, id)
    DEFERRABLE INITIALLY DEFERRED;
CREATE INDEX cloud_run_leases_expiration ON cloud_run_leases(workspace_id, expires_at) WHERE revoked_at IS NULL;

CREATE TABLE cloud_remote_commands (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    requested_by_user_id TEXT NOT NULL REFERENCES users(id),
    client_operation_id TEXT NOT NULL,
    target_agent_id TEXT NOT NULL,
    target_goal_id TEXT,
    payload_json JSONB NOT NULL CHECK (jsonb_typeof(payload_json) = 'object'),
    payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    required_revision BIGINT NOT NULL CHECK (required_revision >= 1),
    state TEXT NOT NULL CHECK (state IN ('queued','claimed','running','completed','failed','cancelled','unknown')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    expires_at TIMESTAMPTZ NOT NULL,
    claimed_lease_id TEXT,
    PRIMARY KEY (workspace_id, id),
    UNIQUE (workspace_id, requested_by_user_id, client_operation_id),
    FOREIGN KEY (workspace_id, target_agent_id) REFERENCES agents(workspace_id, id),
    FOREIGN KEY (workspace_id, target_goal_id) REFERENCES goals(workspace_id, id),
    FOREIGN KEY (workspace_id, claimed_lease_id) REFERENCES cloud_run_leases(workspace_id, id)
);
CREATE INDEX cloud_remote_commands_pending ON cloud_remote_commands(workspace_id, state, created_at);
CREATE TABLE cloud_remote_identities (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    provider TEXT NOT NULL,
    provider_account_key TEXT NOT NULL,
    external_subject TEXT NOT NULL,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    revoked_at TIMESTAMPTZ,
    UNIQUE (provider, provider_account_key, external_subject),
    FOREIGN KEY (account_id, user_id) REFERENCES account_memberships(account_id, user_id)
);
CREATE TABLE cloud_provider_inbox (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    workspace_id TEXT,
    provider TEXT NOT NULL,
    provider_account_key TEXT NOT NULL,
    provider_event_id TEXT NOT NULL,
    body_sha256 TEXT NOT NULL CHECK (body_sha256 ~ '^[0-9a-f]{64}$'),
    sanitized_envelope_json JSONB NOT NULL CHECK (jsonb_typeof(sanitized_envelope_json) = 'object'),
    state TEXT NOT NULL CHECK (state IN ('received','rejected','needs_routing','command_created')),
    command_id TEXT,
    received_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    UNIQUE (account_id, provider, provider_account_key, provider_event_id),
    CHECK (command_id IS NULL OR workspace_id IS NOT NULL),
    FOREIGN KEY (account_id, workspace_id) REFERENCES workspaces(account_id, id),
    FOREIGN KEY (workspace_id, command_id) REFERENCES cloud_remote_commands(workspace_id, id)
    -- Provider callbacks must be authenticated, then mapped through a linked
    -- identity and CURRENT workspace ACL before creating a remote command.
);
CREATE TABLE cloud_command_receipts (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    lease_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    stage TEXT NOT NULL CHECK (stage IN ('received','started','completed','failed','unknown')),
    body_sha256 TEXT NOT NULL CHECK (body_sha256 ~ '^[0-9a-f]{64}$'),
    receipt_json JSONB NOT NULL CHECK (jsonb_typeof(receipt_json) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (workspace_id, id),
    UNIQUE (workspace_id, command_id, lease_id, stage),
    FOREIGN KEY (workspace_id, command_id) REFERENCES cloud_remote_commands(workspace_id, id),
    FOREIGN KEY (workspace_id, lease_id, device_id) REFERENCES cloud_run_leases(workspace_id, id, holder_device_id)
    -- device_id must equal lease holder; a receipt cannot prove an external
    -- action happened only once. Validate authenticated actor on every retry.
);
CREATE TABLE cloud_delivery_jobs (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    recipient_user_id TEXT NOT NULL REFERENCES users(id),
    channel TEXT NOT NULL,
    logical_event_id TEXT NOT NULL,
    source_revision BIGINT NOT NULL CHECK (source_revision >= 1),
    payload_json JSONB NOT NULL CHECK (jsonb_typeof(payload_json) = 'object'),
    state TEXT NOT NULL CHECK (state IN ('queued','sending','accepted','delivered','unknown','failed','cancelled')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    not_before TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    PRIMARY KEY (workspace_id, id),
    UNIQUE (workspace_id, recipient_user_id, channel, logical_event_id)
);
CREATE INDEX cloud_delivery_jobs_pending ON cloud_delivery_jobs(state, not_before);
CREATE TABLE cloud_delivery_attempts (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
    provider_idempotency_key TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    finished_at TIMESTAMPTZ,
    result TEXT CHECK (result IN ('accepted','delivered','failed','unknown')),
    provider_message_id TEXT,
    receipt_json JSONB CHECK (receipt_json IS NULL OR jsonb_typeof(receipt_json) = 'object'),
    error_code TEXT,
    PRIMARY KEY (workspace_id, id),
    UNIQUE (workspace_id, job_id, attempt_number),
    FOREIGN KEY (workspace_id, job_id) REFERENCES cloud_delivery_jobs(workspace_id, id)
);

CREATE TABLE cloud_retention_policies (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    sync_log_days INTEGER NOT NULL CHECK (sync_log_days >= 1),
    inactive_device_days INTEGER NOT NULL CHECK (inactive_device_days >= 1),
    backup_days INTEGER NOT NULL CHECK (backup_days >= 0),
    tombstone_days INTEGER NOT NULL CHECK (tombstone_days >= sync_log_days),
    live_purge_grace_hours INTEGER NOT NULL CHECK (live_purge_grace_hours >= 0),
    effective_at TIMESTAMPTZ NOT NULL,
    superseded_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX cloud_one_active_retention_policy ON cloud_retention_policies(account_id) WHERE superseded_at IS NULL;
CREATE TABLE cloud_purge_requests (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    account_id TEXT REFERENCES accounts(id),
    requested_by_user_id TEXT REFERENCES users(id),
    retention_policy_id TEXT NOT NULL REFERENCES cloud_retention_policies(id),
    deletion_epoch BIGINT NOT NULL CHECK (deletion_epoch >= 1),
    state TEXT NOT NULL CHECK (state IN ('requested','frozen','purging','live_purged','backups_pending','completed','failed')),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    eligible_at TIMESTAMPTZ NOT NULL,
    live_purged_at TIMESTAMPTZ,
    backups_expire_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error_code TEXT,
    UNIQUE (workspace_id, deletion_epoch)
    -- No workspace FK: minimal deletion receipts outlive purged domain rows.
);
CREATE TABLE cloud_retention_holds (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    authority_reference TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('workspace','backups')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp(),
    expires_at TIMESTAMPTZ,
    released_at TIMESTAMPTZ
);
CREATE TABLE cloud_workspace_graves (
    workspace_id TEXT PRIMARY KEY,
    authority_epoch BIGINT NOT NULL CHECK (authority_epoch >= 1),
    deletion_epoch BIGINT NOT NULL CHECK (deletion_epoch >= 1),
    retired_at TIMESTAMPTZ NOT NULL,
    payload_purged_at TIMESTAMPTZ,
    -- Deliberately no title, message body, member roster, path or credential.
    last_accepted_seq BIGINT NOT NULL CHECK (last_accepted_seq >= 0)
);
CREATE TABLE cloud_entity_graves (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
    entity_kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    last_revision BIGINT NOT NULL CHECK (last_revision >= 1),
    deletion_epoch BIGINT NOT NULL CHECK (deletion_epoch >= 1),
    deleted_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (workspace_id, entity_kind, entity_id)
    -- Minimal no-payload identity retirement survives tombstone/log compaction.
    -- Explicit restore may clear it only under CAS and current authorization.
);
CREATE TABLE cloud_audit_events (
    id TEXT PRIMARY KEY,
    account_id TEXT,
    workspace_id TEXT,
    actor_user_id TEXT,
    device_id TEXT,
    operation_id TEXT,
    kind TEXT NOT NULL,
    detail_json JSONB NOT NULL CHECK (jsonb_typeof(detail_json) = 'object'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT transaction_timestamp()
    -- Sanitized audit only. It is NOT a change log or a state reconstruction API.
);

-- Optional defense-in-depth SELECT policy for service-authenticated requests.
-- The service sets crew.actor_user_id via SET LOCAL only after verifying the
-- bearer session. End users receive no DB connection/role or arbitrary SQL.
-- Mutations are admitted only by the server committer, not direct client SQL.
CREATE FUNCTION cloud_can_read_workspace(target_workspace TEXT) RETURNS BOOLEAN
LANGUAGE SQL STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.workspace_memberships wm
        JOIN public.workspaces w ON w.id = wm.workspace_id AND w.account_id = wm.account_id
        JOIN public.account_memberships am ON am.account_id = wm.account_id AND am.user_id = wm.user_id
        JOIN public.cloud_auth_sessions auth ON auth.id = current_setting('crew.auth_session_id', true)
            AND auth.user_id = wm.user_id AND auth.account_id = wm.account_id
        WHERE wm.workspace_id = target_workspace
          AND wm.user_id = current_setting('crew.actor_user_id', true)
          AND wm.account_id = current_setting('crew.actor_account_id', true)
          AND auth.revoked_at IS NULL AND auth.expires_at > statement_timestamp()
          AND wm.deleted_at IS NULL AND am.deleted_at IS NULL AND w.deleted_at IS NULL
    );
$$;
ALTER TABLE cloud_commits ENABLE ROW LEVEL SECURITY;
ALTER TABLE cloud_change_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE cloud_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY cloud_commits_read ON cloud_commits FOR SELECT USING (cloud_can_read_workspace(workspace_id));
CREATE POLICY cloud_changes_read ON cloud_change_items FOR SELECT USING (cloud_can_read_workspace(workspace_id));
CREATE POLICY cloud_snapshots_read ON cloud_snapshots FOR SELECT USING (cloud_can_read_workspace(workspace_id));
-- Deployment must use a non-owner, non-BYPASSRLS read role, restrict function
-- ownership/search_path, and grant writes only through its trusted committer.
-- This DDL intentionally does not create deployment users or runtime services.
