-- Storage v2 PostgreSQL security BASELINE, after postgres-domain.sql + cloud.sql.
-- For a dedicated Crew database using the public schema only. Never apply this
-- to an unrelated shared public schema. This is design DDL, not deployment.
-- No login roles, credentials or grants to an application principal are created.

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;

-- The helper is owned by a trusted non-login schema/authorization owner. Its
-- table-owner visibility is deliberate: otherwise membership RLS recurses.
-- The query/read principal is NOT that owner and has neither BYPASSRLS nor
-- ownership/SET ROLE rights to it. Do not FORCE RLS onto helper-owned ACL tables
-- without replacing this helper with an independently authorized mechanism.

ALTER TABLE public.workspaces ENABLE ROW LEVEL SECURITY;
CREATE POLICY domain_workspaces_read ON public.workspaces
    FOR SELECT USING (public.cloud_can_read_workspace(id));

-- Explicit domain allowlist. New domain relations must be reviewed and added;
-- a naming convention is not permission to expose arbitrary new relations.
DO $storage_v2_domain_rls$
DECLARE relation_name TEXT;
BEGIN
    FOREACH relation_name IN ARRAY ARRAY[
        'agents', 'artifact_links', 'artifact_versions', 'artifacts', 'blob_objects',
        'channel_digests', 'channel_mentions', 'channel_posts', 'channels', 'comments',
        'context_applications', 'context_items', 'context_manifest_entries',
        'context_manifests', 'context_version_sources', 'context_versions',
        'conversations', 'decision_answers', 'decision_options', 'decision_versions',
        'decisions', 'entity_tombstones', 'executions', 'git_requests', 'git_results',
        'goal_acceptances', 'goal_assignments', 'goal_dependencies', 'goal_requirements',
        'goal_rounds', 'goals', 'group_memberships', 'groups', 'handoff_deliveries',
        'handoffs', 'import_batches', 'legacy_records', 'message_parts',
        'message_positions', 'messages', 'native_archives', 'peer_messages', 'policies',
        'reports', 'todos', 'tool_calls', 'tool_results', 'trust_settings',
        'usage_observations', 'workspace_memberships'
    ] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', relation_name);
        EXECUTE format('CREATE POLICY domain_workspace_read ON public.%I FOR SELECT USING (public.cloud_can_read_workspace(workspace_id))', relation_name);
    END LOOP;
END;
$storage_v2_domain_rls$;

-- Global identity/settings relations get NO read policy and NO ordinary reader
-- grant. Their contents include other accounts and private settings. The trusted
-- service returns only minimal attribution parent closure for an authorized
-- workspace; a package's membership rows never become a login/ACL grant.
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.account_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_settings ENABLE ROW LEVEL SECURITY;

-- Only commits/change_items/snapshots have SELECT policies in cloud.sql.
-- All other coordination/auth/lease/operation-body tables default-deny for a
-- non-owner reader, even if a deployment accidentally grants table SELECT.
DO $storage_v2_cloud_rls$
DECLARE relation_name TEXT;
BEGIN
    FOREACH relation_name IN ARRAY ARRAY[
        'cloud_schema_metadata', 'cloud_auth_identities', 'cloud_account_devices',
        'cloud_auth_sessions', 'cloud_workspace_sequencers', 'cloud_operations',
        'cloud_device_cursors', 'cloud_blob_uploads', 'cloud_blob_pins',
        'cloud_run_fences', 'cloud_run_leases', 'cloud_remote_commands',
        'cloud_remote_identities', 'cloud_provider_inbox', 'cloud_command_receipts',
        'cloud_delivery_jobs', 'cloud_delivery_attempts', 'cloud_retention_policies',
        'cloud_purge_requests', 'cloud_retention_holds', 'cloud_workspace_graves',
        'cloud_entity_graves', 'cloud_audit_events'
    ] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', relation_name);
    END LOOP;
END;
$storage_v2_cloud_rls$;

-- DEPLOYMENT GRANT TEMPLATE (comments, replace principal names deliberately):
--   GRANT CONNECT ON DATABASE <dedicated_database> TO <crew_query_role>;
--   GRANT USAGE ON SCHEMA public TO <crew_query_role>;
--   GRANT EXECUTE ON FUNCTION public.cloud_can_read_workspace(TEXT) TO <crew_query_role>;
--   GRANT SELECT ON public.workspaces, <the explicit DOMAIN workspace allowlist>,
--       public.cloud_commits, public.cloud_change_items, public.cloud_snapshots
--       TO <crew_query_role>;
-- Do NOT GRANT SELECT ON ALL TABLES, users/accounts/account_memberships/settings,
-- auth/session/operation/lease tables, or any mutation privilege to that role.
-- Public attribution and protocol/control status use a separately reviewed
-- authorization service/projection, not raw global-table SELECT.
--
-- The trusted committer writes ONLY after current auth, account/workspace ACL,
-- revision, tombstone, blob and lease checks. This file does not implement that
-- committer or its credentials. NO end-user receives a DB connection, arbitrary
-- SQL endpoint, a context-setter function, or permission to impersonate actors.
-- A request auth audit correlates request_id, verified auth_session_id,
-- actor_user_id, account_id, workspace_id and membership_revision BEFORE SET LOCAL.
-- Set crew.auth_session_id, crew.actor_user_id and crew.actor_account_id only
-- from that verified context; the read helper also checks session revocation.
-- Never
-- treat current_setting('crew.actor_user_id') or device_id as authentication.
