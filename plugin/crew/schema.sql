-- Crew board schema. One database (~/.crew/crew.db) shared by every project.
-- Times are UTC ISO-8601 strings with millisecond precision and a trailing Z.
-- Owned by the main controller; tasks extend it only through docs/DESIGN.md.

-- ---------------------------------------------------------------- board

CREATE TABLE IF NOT EXISTS ai_settings (
    singleton   INTEGER PRIMARY KEY CHECK (singleton = 1),
    ai_json     TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_settings (
    singleton   INTEGER PRIMARY KEY CHECK (singleton = 1),
    codex       TEXT NOT NULL DEFAULT 'workspace-write'
                CHECK (codex IN ('read-only', 'workspace-write', 'danger-full-access')),
    claude      TEXT NOT NULL DEFAULT 'default'
                CHECK (claude IN ('default', 'acceptEdits', 'bypassPermissions')),
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    name        TEXT PRIMARY KEY,
    root        TEXT NOT NULL UNIQUE,      -- absolute path, as given (compare with crew.store.norm_path)
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_themes (
    project     TEXT PRIMARY KEY REFERENCES projects(name),
    theme       TEXT NOT NULL,
    assigned_at TEXT NOT NULL
);

-- A group is one leader and the members it manages. Group name == the leader's crew name.
CREATE TABLE IF NOT EXISTS groups (
    project            TEXT NOT NULL,
    name               TEXT NOT NULL,
    leader_session_id  TEXT,               -- NULL until the leader session registers
    note               TEXT,
    created_at         TEXT NOT NULL,
    closed_at          TEXT,
    PRIMARY KEY (project, name)
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,      -- Claude Code session id
    project         TEXT,                  -- NULL when the cwd is under no enabled project
    crew_name       TEXT,                  -- stable human name (e.g. radar, vision); NULL until joined
    cc_name         TEXT,                  -- Claude Code display name = SendMessage address, if known
    role            TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('member', 'leader')),
    group_name      TEXT,                  -- groups.name this session belongs to (leaders: their own group)
    status          TEXT NOT NULL DEFAULT 'idle' CHECK (status IN ('working', 'waiting', 'idle', 'offline', 'closed')),
    status_note     TEXT,                  -- why waiting, etc.
    task            TEXT,                  -- one line: what it is doing
    cwd             TEXT,
    worktree        TEXT,                  -- git toplevel of cwd
    branch          TEXT,
    entrypoint      TEXT,                  -- claude-vscode / cli / ...
    permission_mode TEXT,
    spawn_id        TEXT,                  -- set when started by crew spawn
    terminal_title  TEXT,                  -- Windows Terminal tab title when spawned
    started_at      TEXT NOT NULL,
    last_event      TEXT,
    last_event_at   TEXT,
    last_prompt     TEXT,                  -- first 200 chars of the last user prompt
    last_message    TEXT,                  -- first 300 chars of the last assistant message
    ended_at        TEXT
);
CREATE INDEX IF NOT EXISTS sessions_project ON sessions (project, crew_name);

-- Crew identity stays stable while each execution tool has its own native id.
CREATE TABLE IF NOT EXISTS session_runtimes (
    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
    runtime TEXT NOT NULL CHECK (runtime IN ('claude', 'codex')),
    native_id TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (runtime, native_id)
);

-- Durable, runtime-neutral peer messages. External Claude sessions consume
-- them through their hooks; managed sessions receive them when idle.
CREATE TABLE IF NOT EXISTS peer_messages (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL REFERENCES projects(name),
    sender_session_id TEXT NOT NULL REFERENCES sessions(session_id),
    target_session_id TEXT NOT NULL REFERENCES sessions(session_id),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS peer_messages_target ON peer_messages(target_session_id, delivered_at);

-- Manual actions are separate from questions that need a choice.
CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL REFERENCES projects(name),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    title TEXT NOT NULL,
    steps TEXT NOT NULL,
    verification TEXT NOT NULL,
    blocking INTEGER NOT NULL DEFAULT 0 CHECK (blocking IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done', 'cancelled', 'withdrawn')),
    note TEXT,
    resolved_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT,
    version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS todos_project ON todos(project, status, created_at);
CREATE TABLE IF NOT EXISTS todo_backfills (
    leader_session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
    notice_id INTEGER REFERENCES leader_notices(id),
    queued_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trust_settings (
    project TEXT PRIMARY KEY REFERENCES projects(name),
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
    goal TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trust_decisions (
    decision_id INTEGER PRIMARY KEY REFERENCES decisions(id),
    reason TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decision_briefs (
    decision_id INTEGER PRIMARY KEY REFERENCES decisions(id),
    title TEXT NOT NULL,
    context TEXT NOT NULL,
    why_user TEXT NOT NULL,
    recommendation_reason TEXT NOT NULL,
    impact TEXT NOT NULL,
    options_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decision_reviews (
    decision_id INTEGER PRIMARY KEY REFERENCES decisions(id),
    notice_id INTEGER REFERENCES leader_notices(id),
    queued_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspace_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL REFERENCES projects(name),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Original deliverable files stay in their project/worktree. Only metadata is
-- indexed here; a user's pin is independent from the submitting AI's choice.
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL REFERENCES projects(name),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    leader_session_id TEXT,
    submitter TEXT NOT NULL,
    group_name TEXT,
    path TEXT NOT NULL,
    path_key TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    kind TEXT NOT NULL CHECK(kind IN ('summary','report','guide','other')),
    ai_important INTEGER NOT NULL DEFAULT 0 CHECK(ai_important IN (0,1)),
    user_important INTEGER CHECK(user_important IN (0,1)),
    source TEXT NOT NULL CHECK(source IN ('manual','journal','report')),
    source_seq INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project,path_key)
);
CREATE INDEX IF NOT EXISTS documents_project ON documents(project,leader_session_id,updated_at);
CREATE TABLE IF NOT EXISTS document_scans (
    project TEXT NOT NULL REFERENCES projects(name),
    source TEXT NOT NULL,
    offset INTEGER NOT NULL DEFAULT 0,
    size INTEGER NOT NULL DEFAULT 0,
    mtime_ns INTEGER NOT NULL DEFAULT 0,
    identity TEXT,
    error TEXT,
    scanned_at TEXT NOT NULL,
    PRIMARY KEY(project,source)
);

CREATE TABLE IF NOT EXISTS leader_order (
    project TEXT NOT NULL REFERENCES projects(name),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    position INTEGER NOT NULL,
    PRIMARY KEY (project, session_id)
);

CREATE TABLE IF NOT EXISTS leader_priority (
    project TEXT NOT NULL REFERENCES projects(name),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    position INTEGER NOT NULL CHECK (position BETWEEN 0 AND 2),
    PRIMARY KEY (project, session_id),
    UNIQUE (project, position)
);

CREATE TABLE IF NOT EXISTS git_management (
    project TEXT PRIMARY KEY REFERENCES projects(name),
    enabled_at TEXT NOT NULL,
    base TEXT NOT NULL,
    stable TEXT,
    admin_session_id TEXT REFERENCES sessions(session_id),
    branch_rules TEXT
);

CREATE TABLE IF NOT EXISTS git_merge_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL REFERENCES projects(name),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    branch TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'merging', 'waiting_user', 'merged', 'rejected')),
    pr_number INTEGER,
    note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS git_merge_requests_project ON git_merge_requests (project, status, id);

CREATE TABLE IF NOT EXISTS git_ci_state (
    project TEXT NOT NULL REFERENCES projects(name),
    subject TEXT NOT NULL,
    run_key TEXT NOT NULL,
    sha TEXT NOT NULL,
    status TEXT NOT NULL,
    notified INTEGER NOT NULL CHECK (notified IN (0, 1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project, subject, run_key)
);

DROP TABLE IF EXISTS session_models;
CREATE TABLE IF NOT EXISTS session_options (
    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
    model TEXT,
    effort TEXT,
    set_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rule_reads (
    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
    role TEXT NOT NULL,
    digest TEXT NOT NULL,
    read_at TEXT NOT NULL
);

-- Explicit human choices survive hooks from the original running process.
CREATE TABLE IF NOT EXISTS session_controls (
    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
    archived_status TEXT,
    archived_note TEXT,
    archived_ended_at TEXT,
    removed INTEGER NOT NULL DEFAULT 0,
    name_override TEXT
);

CREATE TABLE IF NOT EXISTS attention_resolutions (
    item_id TEXT PRIMARY KEY,
    resolved_at TEXT NOT NULL
);

-- A local delivery record ties remote answers to messages actually sent by Crew.
CREATE TABLE IF NOT EXISTS notification_messages (
    item_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    leader_session_id TEXT NOT NULL,
    decision_id INTEGER,
    kind TEXT NOT NULL,
    message_id TEXT,
    ref_idx TEXT,
    created_at TEXT NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS notification_inbound (
    event_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_replies (
    message_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    text TEXT NOT NULL,
    status TEXT NOT NULL,
    after_seq INTEGER,
    created_at TEXT NOT NULL
);

-- The task card a session writes when it is adopted: its area and when it counts as done. sessions.task stays "what now".
CREATE TABLE IF NOT EXISTS session_cards (
    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
    scope      TEXT,
    done_when  TEXT,
    updated_at TEXT NOT NULL
);

-- How far the human has read each web chat journal (leaders). Drives unread counts and the "since you left" divider.
CREATE TABLE IF NOT EXISTS read_cursors (
    session_id TEXT PRIMARY KEY,
    seq        INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

-- Things Crew itself must tell a leader (an answer saved on the web or QQ, a member adopted into its group).
-- The web service writes them into the leader's input once it is idle; the leader relays to members with SendMessage.
CREATE TABLE IF NOT EXISTS leader_notices (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project    TEXT NOT NULL,
    session_id TEXT NOT NULL,              -- the leader
    source     TEXT NOT NULL CHECK (source IN ('web', 'qq', 'crew')),
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sent_at    TEXT
);
CREATE INDEX IF NOT EXISTS leader_notices_pending ON leader_notices (session_id, sent_at);

CREATE TABLE IF NOT EXISTS channel_messages (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    project            TEXT NOT NULL REFERENCES projects(name),
    speaker_session_id TEXT NOT NULL,      -- leader session id, or 'user'
    speaker_name       TEXT NOT NULL,
    body               TEXT NOT NULL,
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS channel_messages_project ON channel_messages (project, id);

CREATE TABLE IF NOT EXISTS channel_mentions (
    message_id INTEGER NOT NULL REFERENCES channel_messages(id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    name       TEXT NOT NULL,
    notice_id  INTEGER REFERENCES leader_notices(id),
    PRIMARY KEY (message_id, session_id)
);

CREATE TABLE IF NOT EXISTS channel_reads (
    project    TEXT NOT NULL REFERENCES projects(name),
    reader     TEXT NOT NULL,             -- a session id, or 'user'
    message_id INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (project, reader)
);

CREATE TABLE IF NOT EXISTS channel_digests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project         TEXT NOT NULL REFERENCES projects(name),
    upto_message_id INTEGER NOT NULL REFERENCES channel_messages(id),
    body            TEXT NOT NULL,
    message_count   INTEGER NOT NULL,
    trigger         TEXT NOT NULL CHECK (trigger IN ('manual', 'auto')),
    model           TEXT NOT NULL,
    runtime         TEXT NOT NULL DEFAULT 'claude' CHECK (runtime IN ('claude', 'codex')),
    cost_usd        REAL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS channel_digests_project ON channel_digests (project, id);

CREATE TABLE IF NOT EXISTS resources (
    project      TEXT NOT NULL,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'device' CHECK (kind IN ('device', 'dir', 'app', 'other')),
    description  TEXT,
    guard_regex  TEXT,                     -- commands matching this are blocked for non-holders while leased
    created_at   TEXT NOT NULL,
    PRIMARY KEY (project, name)
);

CREATE TABLE IF NOT EXISTS leases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project         TEXT NOT NULL,
    resource        TEXT NOT NULL,
    holder_session  TEXT NOT NULL,
    purpose         TEXT NOT NULL,
    overwrites      TEXT,                  -- what using it will overwrite (e.g. installed package)
    taken_at        TEXT NOT NULL,
    until_at        TEXT,                  -- expected return time
    released_at     TEXT,
    released_by     TEXT,                  -- session id, or 'user' from the web page / plain terminal
    release_note    TEXT                   -- state left behind (e.g. which package is installed now)
);
-- First come wins: at most one open lease per resource.
CREATE UNIQUE INDEX IF NOT EXISTS leases_one_open ON leases (project, resource) WHERE released_at IS NULL;

CREATE TABLE IF NOT EXISTS claims (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    pattern     TEXT NOT NULL,             -- normalized; see docs/DESIGN.md "File claims"
    note        TEXT,
    created_at  TEXT NOT NULL,
    released_at TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project         TEXT NOT NULL,
    session_id      TEXT NOT NULL,         -- who asks
    question        TEXT NOT NULL,
    options         TEXT NOT NULL DEFAULT '[]',  -- JSON array of strings
    recommendation  TEXT,
    blocking        INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'answered', 'withdrawn')),
    answer          TEXT,
    answered_by     TEXT,
    created_at      TEXT NOT NULL,
    answered_at     TEXT,
    delivered_at    TEXT                   -- when the answer was injected into the asking session
);

CREATE TABLE IF NOT EXISTS decision_holds (
    decision_id INTEGER PRIMARY KEY REFERENCES decisions(id),
    held_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS handoffs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project       TEXT NOT NULL,
    from_session  TEXT NOT NULL,
    to_target     TEXT NOT NULL,           -- a crew name, a group name, or '*'
    body          TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

-- One row per (handoff, receiving session) once delivered, so '*' and group handoffs reach everyone once.
CREATE TABLE IF NOT EXISTS handoff_deliveries (
    handoff_id    INTEGER NOT NULL,
    session_id    TEXT NOT NULL,
    delivered_at  TEXT NOT NULL,
    PRIMARY KEY (handoff_id, session_id)
);

CREATE TABLE IF NOT EXISTS git_result_handoffs (
    handoff_id INTEGER PRIMARY KEY REFERENCES handoffs(id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    admin_session_id TEXT NOT NULL REFERENCES sessions(session_id)
);

CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project     TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    project     TEXT,
    session_id  TEXT,
    kind        TEXT NOT NULL,             -- hook:<event>, lease:take, lease:give, claim, decision:ask, ...
    detail      TEXT                       -- JSON object
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);

-- ---------------------------------------------------------------- usage

-- Incremental scan bookkeeping: transcripts are append-only JSONL, scanned from the last byte offset.
CREATE TABLE IF NOT EXISTS usage_files (
    path        TEXT PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('claude', 'claude_subagent', 'codex', 'codex_runs')),
    size        INTEGER NOT NULL,
    mtime       REAL NOT NULL,
    offset      INTEGER NOT NULL,          -- bytes consumed (always at a line boundary)
    scanned_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claude_sessions (
    session_id   TEXT PRIMARY KEY,
    project_dir  TEXT NOT NULL,            -- folder name under ~/.claude/projects
    cwd          TEXT,
    entrypoint   TEXT,
    version      TEXT,
    ai_title     TEXT,                     -- latest ai-title record
    first_ts     TEXT,
    last_ts      TEXT
);

-- One row per API request. Transcripts repeat the same message (same usage) on several lines; keep one.
CREATE TABLE IF NOT EXISTS claude_requests (
    request_key        TEXT PRIMARY KEY,   -- message.id, else requestId
    session_id         TEXT NOT NULL,      -- the main session (for subagents: the parent session)
    agent_id           TEXT,               -- subagent id, NULL for the main conversation
    agent_type         TEXT,               -- subagent type from its .meta.json, NULL for main
    ts                 TEXT NOT NULL,
    model              TEXT,
    effort             TEXT,
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_5m     INTEGER NOT NULL DEFAULT 0,
    cache_write_1h     INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    thinking_tokens    INTEGER NOT NULL DEFAULT 0,
    web_search         INTEGER NOT NULL DEFAULT 0,
    web_fetch          INTEGER NOT NULL DEFAULT 0,
    service_tier       TEXT,
    speed              TEXT
);
CREATE INDEX IF NOT EXISTS claude_requests_session ON claude_requests (session_id, ts);
CREATE INDEX IF NOT EXISTS claude_requests_ts ON claude_requests (ts);

-- Turn starts. Tool results and meta lines are not prompts.
-- kind comes from the record's origin.kind: human -> user, peer -> peer, task-notification -> task.
CREATE TABLE IF NOT EXISTS claude_prompts (
    uuid        TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('user', 'peer', 'task')),
    chars       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS claude_prompts_session ON claude_prompts (session_id, ts);

CREATE TABLE IF NOT EXISTS claude_tool_calls (
    tool_use_id  TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    agent_id     TEXT,
    ts           TEXT NOT NULL,
    tool_name    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claude_compactions (
    uuid        TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    ts          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS codex_sessions (
    rollout_id          TEXT PRIMARY KEY,
    path                TEXT NOT NULL,
    started_at          TEXT,
    ended_at            TEXT,              -- timestamp of the last record seen
    cwd                 TEXT,
    originator          TEXT,              -- codex_exec, desktop app, ...
    source              TEXT,
    cli_version         TEXT,
    model               TEXT,
    effort              TEXT,
    sandbox             TEXT,
    input_tokens        INTEGER NOT NULL DEFAULT 0,  -- latest total_token_usage
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens    INTEGER NOT NULL DEFAULT 0,
    total_tokens        INTEGER NOT NULL DEFAULT 0,
    first_user_text     TEXT               -- first 200 chars of the first user message (task title)
);

-- Lines of ~/.crew/codex_runs.jsonl written by the codex-dispatch script.
CREATE TABLE IF NOT EXISTS codex_runs (
    run_id             TEXT PRIMARY KEY,
    claude_session_id  TEXT,               -- dispatching Claude session (CLAUDE_CODE_SESSION_ID)
    task_file          TEXT NOT NULL,
    workdir            TEXT NOT NULL,
    sandbox            TEXT,
    model              TEXT,
    effort             TEXT,
    started_at         TEXT NOT NULL,
    ended_at           TEXT,
    exit_code          INTEGER,
    rollout_id         TEXT                -- matched codex_sessions row
);

-- One row per token_count event carrying rate_limits. Several rollouts can report in the same millisecond.
CREATE TABLE IF NOT EXISTS codex_rate_limits (
    rollout_id       TEXT NOT NULL,
    ts               TEXT NOT NULL,
    plan_type        TEXT,
    used_percent     REAL,
    window_minutes   INTEGER,
    resets_at        TEXT,
    credits_balance  TEXT,
    PRIMARY KEY (rollout_id, ts)
);

-- A Crew identity can return to each runtime's own native history/options.
CREATE TABLE IF NOT EXISTS session_runtime_bindings (
 session_id TEXT NOT NULL REFERENCES sessions(session_id),
 runtime TEXT NOT NULL CHECK(runtime IN ('claude','codex')),
 native_id TEXT,
 model TEXT,
 effort TEXT,
 updated_at TEXT NOT NULL,
 PRIMARY KEY(session_id,runtime),
 UNIQUE(runtime,native_id)
);
CREATE TABLE IF NOT EXISTS runtime_switches (
 id TEXT PRIMARY KEY,
 session_id TEXT NOT NULL REFERENCES sessions(session_id),
 project TEXT NOT NULL REFERENCES projects(name),
 from_runtime TEXT NOT NULL CHECK(from_runtime IN ('claude','codex')),
 to_runtime TEXT NOT NULL CHECK(to_runtime IN ('claude','codex')),
 target_model TEXT,
 target_effort TEXT,
 revision TEXT NOT NULL,
 handoff TEXT NOT NULL,
 created_at TEXT NOT NULL,
 expires_at TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('preview','configured')),
 preview_json TEXT NOT NULL,
 configured_at TEXT,
 result_json TEXT,
 superseded_at TEXT,
 attempted_message_id TEXT,
 attempted_at TEXT,
 accepted_message_id TEXT,
 consumed_at TEXT,
 last_error TEXT
);
CREATE INDEX IF NOT EXISTS runtime_switches_session ON runtime_switches(session_id,status,configured_at);

-- Leader-scoped trust. Legacy project settings seed only the leaders present
-- at upgrade time; later leaders start in ordinary mode.
CREATE TABLE IF NOT EXISTS leader_trust_settings (
 leader_session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
 project TEXT NOT NULL REFERENCES projects(name),
 enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
 goal TEXT,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trust_leader_migrations (
 project TEXT PRIMARY KEY REFERENCES projects(name), migrated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO leader_trust_settings
 SELECT s.session_id,s.project,t.enabled,t.goal,t.updated_at FROM sessions s
 JOIN trust_settings t ON t.project=s.project WHERE s.role='leader'
 AND NOT EXISTS(SELECT 1 FROM trust_leader_migrations m WHERE m.project=s.project);
INSERT OR IGNORE INTO trust_leader_migrations
 SELECT name,strftime('%Y-%m-%dT%H:%M:%fZ','now') FROM projects;
CREATE TABLE IF NOT EXISTS decision_leader_scopes (
 decision_id INTEGER PRIMARY KEY REFERENCES decisions(id),
 leader_session_id TEXT REFERENCES sessions(session_id)
);
CREATE TABLE IF NOT EXISTS trust_schema_migrations (
 name TEXT PRIMARY KEY, applied_at TEXT NOT NULL
);
-- Historical scopes reflect upgrade-time membership, not reconstructed past
-- membership. NULL is deliberate and never guessed again after migration.
INSERT OR IGNORE INTO decision_leader_scopes
 SELECT d.id,CASE WHEN s.role='leader' THEN s.session_id ELSE
   (SELECT g.leader_session_id FROM groups g WHERE g.project=s.project AND g.name=s.group_name) END
 FROM decisions d LEFT JOIN sessions s ON s.session_id=d.session_id
 WHERE NOT EXISTS(SELECT 1 FROM trust_schema_migrations WHERE name='decision-leader-v1');
DELETE FROM leader_notices
 WHERE source='crew' AND sent_at IS NULL AND id IN
 (SELECT n.id FROM events e JOIN json_each(CASE WHEN json_valid(e.detail) THEN e.detail ELSE '{}' END,'$.notice_ids') j
  JOIN leader_notices n ON n.id=j.value AND n.project=e.project
  WHERE e.kind='trust:set' AND j.type='integer'
  AND NOT EXISTS(SELECT 1 FROM trust_schema_migrations WHERE name='decision-leader-v1'));
INSERT OR IGNORE INTO trust_schema_migrations
 VALUES('decision-leader-v1',strftime('%Y-%m-%dT%H:%M:%fZ','now'));

-- Goal delivery keeps stable Crew identities separate from native executions.
CREATE TABLE IF NOT EXISTS goals (
 id TEXT PRIMARY KEY, project TEXT NOT NULL REFERENCES projects(name),
 leader_session_id TEXT NOT NULL REFERENCES sessions(session_id), title TEXT NOT NULL,
 original_request TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN
 ('queued','running','pausing','paused','waiting_user','awaiting_acceptance','completed','failed','cancelled')),
 requirement_version INTEGER NOT NULL, current_round INTEGER NOT NULL,
 priority INTEGER NOT NULL DEFAULT 0, dependencies_json TEXT NOT NULL DEFAULT '[]',
 manual_pause INTEGER NOT NULL DEFAULT 0 CHECK(manual_pause IN (0,1)), reason TEXT,
 version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS goals_project_owner ON goals(project,leader_session_id,status);
CREATE TABLE IF NOT EXISTS goal_requirements (
 goal_id TEXT NOT NULL REFERENCES goals(id), version INTEGER NOT NULL,
 request TEXT NOT NULL, acceptance_json TEXT NOT NULL, constraints_json TEXT NOT NULL,
 change_text TEXT NOT NULL, actor TEXT NOT NULL, source_json TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(goal_id,version)
);
CREATE TABLE IF NOT EXISTS goal_rounds (
 goal_id TEXT NOT NULL REFERENCES goals(id), number INTEGER NOT NULL,
 requirement_version INTEGER NOT NULL, status TEXT NOT NULL, outcome TEXT, reason TEXT,
 opened_at TEXT NOT NULL, ended_at TEXT, PRIMARY KEY(goal_id,number)
);
CREATE TABLE IF NOT EXISTS goal_assignments (
 goal_id TEXT NOT NULL REFERENCES goals(id), session_id TEXT NOT NULL REFERENCES sessions(session_id),
 task TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('assigned','released')),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(goal_id,session_id)
);
CREATE TABLE IF NOT EXISTS goal_executions (
 id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, round INTEGER NOT NULL,
 session_id TEXT NOT NULL REFERENCES sessions(session_id), generation INTEGER NOT NULL,
 runtime TEXT NOT NULL CHECK(runtime IN ('claude','codex')), model TEXT, effort TEXT,
 status TEXT NOT NULL, requirement_version INTEGER NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, ended_at TEXT, error TEXT,
 FOREIGN KEY(goal_id,round) REFERENCES goal_rounds(goal_id,number),
 UNIQUE(goal_id,round,session_id,generation)
);
CREATE INDEX IF NOT EXISTS goal_executions_goal ON goal_executions(goal_id,round,status);
CREATE TABLE IF NOT EXISTS goal_execution_bindings (
 execution_id TEXT NOT NULL REFERENCES goal_executions(id),
 runtime TEXT NOT NULL CHECK(runtime IN ('claude','codex')), native_id TEXT,
 model TEXT, effort TEXT, updated_at TEXT NOT NULL,
 PRIMARY KEY(execution_id,runtime), UNIQUE(runtime,native_id)
);
CREATE TABLE IF NOT EXISTS goal_reports (
 id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, round INTEGER NOT NULL,
 requirement_version INTEGER NOT NULL, version INTEGER NOT NULL,
 outcome TEXT NOT NULL, data_json TEXT NOT NULL, html TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(goal_id,round) REFERENCES goal_rounds(goal_id,number), UNIQUE(goal_id,round,version)
);
CREATE TABLE IF NOT EXISTS goal_context_items (
 id TEXT PRIMARY KEY, project TEXT REFERENCES projects(name), goal_id TEXT REFERENCES goals(id),
 leader_session_id TEXT REFERENCES sessions(session_id), scope TEXT NOT NULL,
 kind TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
 protected INTEGER NOT NULL CHECK(protected IN (0,1)), created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS goal_context_scope ON goal_context_items(project,goal_id,scope,state);
CREATE TABLE IF NOT EXISTS goal_context_versions (
 item_id TEXT NOT NULL REFERENCES goal_context_items(id), version INTEGER NOT NULL,
 text TEXT NOT NULL, source_json TEXT NOT NULL, depends_json TEXT NOT NULL,
 state TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL,
 PRIMARY KEY(item_id,version)
);
CREATE TABLE IF NOT EXISTS goal_context_manifests (
 id TEXT PRIMARY KEY, execution_id TEXT NOT NULL REFERENCES goal_executions(id),
 goal_id TEXT NOT NULL REFERENCES goals(id), round INTEGER NOT NULL,
 requirement_version INTEGER NOT NULL, generation INTEGER NOT NULL,
 manifest_json TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL, applied_at TEXT
);
CREATE TABLE IF NOT EXISTS goal_context_applied (
 execution_id TEXT NOT NULL REFERENCES goal_executions(id), material_key TEXT NOT NULL,
 version TEXT NOT NULL, manifest_id TEXT NOT NULL REFERENCES goal_context_manifests(id),
 applied_at TEXT NOT NULL, PRIMARY KEY(execution_id,material_key)
);
CREATE TABLE IF NOT EXISTS goal_execution_reads (
 execution_id TEXT PRIMARY KEY REFERENCES goal_executions(id),
 read_seq INTEGER NOT NULL DEFAULT 0 CHECK(read_seq>=0), updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS goal_message_meta (
 message_id TEXT PRIMARY KEY, goal_id TEXT NOT NULL REFERENCES goals(id),
 round INTEGER NOT NULL, requirement_version INTEGER NOT NULL,
 source_execution TEXT REFERENCES goal_executions(id), target_execution TEXT REFERENCES goal_executions(id),
 kind TEXT NOT NULL, correlation_id TEXT NOT NULL, causation_id TEXT,
 idempotency_key TEXT NOT NULL UNIQUE,
 requires_reply INTEGER NOT NULL DEFAULT 0 CHECK(requires_reply IN (0,1)),
 executable INTEGER NOT NULL DEFAULT 0 CHECK(executable IN (0,1)), status TEXT NOT NULL DEFAULT 'queued',
 attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 last_attempt_at TEXT, accepted_at TEXT, error TEXT
);
CREATE INDEX IF NOT EXISTS goal_message_dispatch ON goal_message_meta(goal_id,round,status,target_execution);
CREATE TABLE IF NOT EXISTS goal_control_revisions (
 goal_id TEXT PRIMARY KEY REFERENCES goals(id), revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS goal_ops_pauses (
 goal_id TEXT NOT NULL REFERENCES goals(id), incident_id TEXT NOT NULL,
 token_json TEXT NOT NULL, created_at TEXT NOT NULL, resumed_at TEXT,
 PRIMARY KEY(goal_id,incident_id)
);
CREATE TABLE IF NOT EXISTS goal_round_finishes (
 goal_id TEXT NOT NULL REFERENCES goals(id), round INTEGER NOT NULL,
 requirement_version INTEGER NOT NULL, report_id TEXT NOT NULL REFERENCES goal_reports(id),
 outcome TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','finalized','superseded')),
 actor TEXT NOT NULL, execution_id TEXT REFERENCES goal_executions(id),
 requested_at TEXT NOT NULL, finalized_at TEXT,
 PRIMARY KEY(goal_id,round)
);
CREATE TABLE IF NOT EXISTS goal_resumption_deliveries (
 goal_id TEXT NOT NULL REFERENCES goals(id), incident_id TEXT NOT NULL,
 execution_id TEXT NOT NULL REFERENCES goal_executions(id), message_id TEXT NOT NULL,
 accepted_at TEXT NOT NULL, PRIMARY KEY(goal_id,incident_id,execution_id)
);
CREATE TABLE IF NOT EXISTS goal_links (
 kind TEXT NOT NULL, item_id INTEGER NOT NULL,
 goal_id TEXT NOT NULL REFERENCES goals(id), round INTEGER NOT NULL,
 requirement_version INTEGER NOT NULL, execution_id TEXT NOT NULL REFERENCES goal_executions(id),
 delivered_version INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(kind,item_id)
);
CREATE TABLE IF NOT EXISTS goal_cancellations (
 goal_id TEXT PRIMARY KEY REFERENCES goals(id), round INTEGER NOT NULL,
 requested_at TEXT NOT NULL, actor TEXT NOT NULL
);
