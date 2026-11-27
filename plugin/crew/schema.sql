-- Crew board schema. One database (~/.crew/crew.db) shared by every project.
-- Times are UTC ISO-8601 strings with millisecond precision and a trailing Z.
-- Owned by the main controller; tasks extend it only through docs/DESIGN.md.

-- ---------------------------------------------------------------- board

CREATE TABLE IF NOT EXISTS ai_settings (
    singleton   INTEGER PRIMARY KEY CHECK (singleton = 1),
    ai_json     TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- New purposes do not change the JSON contract read by an older running service.
CREATE TABLE IF NOT EXISTS ai_task_settings (
    purpose        TEXT PRIMARY KEY,
    selection_json TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leader_member_settings (
    leader_session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
    selection_json    TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_settings (
    singleton   INTEGER PRIMARY KEY CHECK (singleton = 1),
    codex       TEXT NOT NULL DEFAULT 'workspace-write'
                CHECK (codex IN ('read-only', 'workspace-write', 'danger-full-access')),
    claude      TEXT NOT NULL DEFAULT 'default'
                CHECK (claude IN ('default', 'acceptEdits', 'bypassPermissions')),
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sound_settings (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    preferences_json TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS canvas_settings (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    wheel_zoom INTEGER NOT NULL CHECK(wheel_zoom IN (0,1))
);

-- First-run guide on the desktop home: once finished or skipped it is never shown automatically again.
CREATE TABLE IF NOT EXISTS onboarding_state (
    singleton  INTEGER PRIMARY KEY CHECK (singleton = 1),
    status     TEXT NOT NULL CHECK (status IN ('completed', 'skipped')),
    updated_at TEXT NOT NULL
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
CREATE TABLE IF NOT EXISTS workspace_assistants (
 project TEXT PRIMARY KEY REFERENCES projects(name),
 session_id TEXT NOT NULL UNIQUE REFERENCES sessions(session_id),
 created_at TEXT NOT NULL
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
    error TEXT,
    kind TEXT,                         -- NULL retains the meaning of legacy untyped mail
    reply_to TEXT REFERENCES peer_messages(id),
    accepted INTEGER NOT NULL DEFAULT 0 -- handed to native input; not execution completion
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
CREATE TABLE IF NOT EXISTS todo_operation_keys (
 project TEXT NOT NULL REFERENCES projects(name), operation_key TEXT NOT NULL,
 todo_id INTEGER NOT NULL REFERENCES todos(id), PRIMARY KEY(project,operation_key)
);
CREATE TABLE IF NOT EXISTS todo_backfills (
    leader_session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
    notice_id INTEGER REFERENCES leader_notices(id),
    queued_at TEXT NOT NULL
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

-- Pinned leaders show first; among themselves they keep the leader_order order.
CREATE TABLE IF NOT EXISTS leader_pins (
    project TEXT NOT NULL REFERENCES projects(name),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    PRIMARY KEY (project, session_id)
);

CREATE TABLE IF NOT EXISTS git_management (
    project TEXT PRIMARY KEY REFERENCES projects(name),
    enabled_at TEXT NOT NULL,
    base TEXT NOT NULL,
    admin_session_id TEXT REFERENCES sessions(session_id),
    branch_rules TEXT,
    integration TEXT
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
    blocking INTEGER NOT NULL DEFAULT 0 CHECK (blocking IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Who created or committed on a branch, seen in the shell commands Crew's hooks allow.
-- goal_id is the task the session was executing then, when it was one.
CREATE TABLE IF NOT EXISTS branch_claims (
    project TEXT NOT NULL REFERENCES projects(name),
    branch TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    goal_id TEXT,
    claimed_at TEXT NOT NULL,
    PRIMARY KEY (project, branch, session_id)
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

CREATE TABLE IF NOT EXISTS session_options (
    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
    model TEXT,
    effort TEXT,
    set_at TEXT NOT NULL,
    speed TEXT
);

CREATE TABLE IF NOT EXISTS rule_reads (
    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
    role TEXT NOT NULL,
    digest TEXT NOT NULL,
    read_at TEXT NOT NULL
);

-- A leader's chosen color and compressed avatar (WebP/PNG/JPEG, at most 200KB).
CREATE TABLE IF NOT EXISTS session_appearance (
    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
    color TEXT,
    avatar BLOB,
    avatar_type TEXT,
    avatar_version INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
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
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt REAL,
    delivery_json TEXT
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

-- Lark has independent owners, delivery receipts and inbound deduplication.
CREATE TABLE IF NOT EXISTS lark_notification_messages (
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
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt REAL,
    delivery_json TEXT
);

CREATE TABLE IF NOT EXISTS lark_notification_inbound (
    event_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lark_notification_replies (
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
    source     TEXT NOT NULL CHECK (source IN ('web', 'qq', 'lark', 'crew')),
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sent_at    TEXT,
    wake       INTEGER NOT NULL DEFAULT 1,  -- 0: wait for the leader's next conversation instead of starting a turn
    delivered_via TEXT CHECK (delivered_via IN ('hook')),  -- NULL: written into the leader's input; hook: carried by an external session's prompt hook
    batch_id   TEXT,                       -- the web service's input batch that owns delivering it; the hook never takes these
    waiting    TEXT CHECK (waiting IN ('external', 'busy', 'stopped', 'maintenance', 'liveness_unknown')
                           OR waiting GLOB 'failed|?*|?*')  -- failure also carries the attempt time and error
);
CREATE INDEX IF NOT EXISTS leader_notices_pending ON leader_notices (session_id, sent_at);

CREATE TABLE IF NOT EXISTS channel_messages (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    project            TEXT NOT NULL REFERENCES projects(name),
    speaker_session_id TEXT NOT NULL,      -- leader session id, or 'user'
    speaker_name       TEXT NOT NULL,
    body               TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    reply_to           INTEGER REFERENCES channel_messages(id),  -- the post this one answers
    detail             INTEGER NOT NULL DEFAULT 0,  -- 1: the speaker wrote the full answer in its own conversation
    auto               INTEGER NOT NULL DEFAULT 0,  -- 1: Crew posted it because the leader answered only in private
    room_id            INTEGER REFERENCES war_rooms(id)  -- set: the post belongs to that war room's timeline
);
CREATE INDEX IF NOT EXISTS channel_messages_project ON channel_messages (project, id);

-- A war room is a channel topic with fixed members, a shared board and one decision card (docs/WAR_ROOM_DESIGN.md).
CREATE TABLE IF NOT EXISTS war_rooms (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    project             TEXT NOT NULL REFERENCES projects(name),
    title               TEXT NOT NULL,
    goal                TEXT NOT NULL,
    lead_session_id     TEXT NOT NULL,
    origin_message_id   INTEGER REFERENCES channel_messages(id),  -- the channel post it was raised from
    opened_message_id   INTEGER REFERENCES channel_messages(id),  -- the main-channel notice that it opened
    closed_message_id   INTEGER REFERENCES channel_messages(id),  -- the main-channel notice that it closed
    contract            TEXT NOT NULL DEFAULT '',
    contract_updated_at TEXT,
    contract_updated_by TEXT,
    status              TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    summary             TEXT,
    created_at          TEXT NOT NULL,
    closed_at           TEXT,
    budget_tokens       INTEGER,                    -- NULL: no cap; else the members' tokens this room may spend
    budget_base         TEXT NOT NULL DEFAULT '{}', -- Codex rollout totals when each member joined (counted from there)
    paused_at           TEXT                        -- the cap was reached; members stopped until the user goes on
);
CREATE INDEX IF NOT EXISTS war_rooms_project ON war_rooms (project, status);
CREATE TABLE IF NOT EXISTS war_room_members (
    room_id    INTEGER NOT NULL REFERENCES war_rooms(id),
    session_id TEXT NOT NULL,
    joined_at  TEXT NOT NULL,
    left_at    TEXT,
    PRIMARY KEY (room_id, session_id)
);
CREATE TABLE IF NOT EXISTS war_room_items (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id          INTEGER NOT NULL REFERENCES war_rooms(id),
    title            TEXT NOT NULL,
    owner_session_id TEXT,
    status           TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo', 'doing', 'blocked', 'done')),
    note             TEXT,
    updated_at       TEXT NOT NULL,
    updated_by       TEXT NOT NULL
);
-- Board and membership changes, shown as handoff points on the room timeline.
CREATE TABLE IF NOT EXISTS war_room_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id    INTEGER NOT NULL REFERENCES war_rooms(id),
    at         TEXT NOT NULL,
    actor      TEXT NOT NULL,
    kind       TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS war_room_decisions (
    decision_id INTEGER PRIMARY KEY REFERENCES decisions(id),
    room_id     INTEGER NOT NULL REFERENCES war_rooms(id)
);
CREATE TABLE IF NOT EXISTS war_room_reads (
    room_id    INTEGER NOT NULL REFERENCES war_rooms(id),
    reader     TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (room_id, reader)
);

CREATE TABLE IF NOT EXISTS channel_mentions (
    message_id INTEGER NOT NULL REFERENCES channel_messages(id),
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    name       TEXT NOT NULL,
    notice_id  INTEGER REFERENCES leader_notices(id),
    await_reply INTEGER NOT NULL DEFAULT 0,  -- 1: the user named this leader and it has not answered in the channel yet
    reason     TEXT NOT NULL DEFAULT 'mention' CHECK (reason IN ('mention', 'reply')),  -- reply: told because the post answers its own
    PRIMARY KEY (message_id, session_id)
);
-- A notice's delivery change finds the posts that show it (change_triggers.py).
CREATE INDEX IF NOT EXISTS channel_mentions_notice ON channel_mentions (notice_id);

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

-- Retired "hold" marks (feature removed 2026-11-11). Business code no longer reads or writes them;
-- the table stays so existing rows survive and storage_v2 packages keep their held_at round trip.
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
    admin_session_id TEXT NOT NULL REFERENCES sessions(session_id),
    request_id INTEGER REFERENCES git_merge_requests(id)
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

-- Incremental changes behind the snapshot + sequence stream (docs/EVENT_STREAM_DESIGN.md).
-- Written in the same transaction as the business write (or by the card triggers, change_triggers.py);
-- a row says which entity changed, and readers build its current value. `events` above stays the audit log.
-- `seq` never repeats (AUTOINCREMENT) and SQLite's single writer commits rows in seq order.
-- Pruning only removes the oldest rows, so the pruned watermark is min(seq) - 1, or the last
-- assigned seq (sqlite_sequence) once every row is gone; no separate state row is written.
CREATE TABLE IF NOT EXISTS change_log (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    scope       TEXT NOT NULL,             -- 'global' or 'workspace:<project name>'
    type        TEXT NOT NULL,             -- '<kind>.invalidated'; '<kind>.updated' for the usage scan's limits (and rows written before invalidations)
    entity      TEXT NOT NULL,             -- the entity id inside its kind, as text
    data        TEXT                       -- NULL for invalidations; the JSON value for '.updated' rows
);
CREATE INDEX IF NOT EXISTS change_log_at ON change_log (at);
CREATE INDEX IF NOT EXISTS change_log_scope ON change_log (scope, seq);

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
    speed               TEXT,
    sandbox             TEXT,
    input_tokens        INTEGER NOT NULL DEFAULT 0,  -- latest total_token_usage
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens    INTEGER NOT NULL DEFAULT 0,
    total_tokens        INTEGER NOT NULL DEFAULT 0,
    first_user_text     TEXT               -- first 200 chars of the first user message (task title)
);

-- Skill dispatch receipts. Historical table name; runtime distinguishes both carriers.
CREATE TABLE IF NOT EXISTS codex_runs (
    run_id             TEXT PRIMARY KEY,
    claude_session_id  TEXT,               -- dispatching Claude session (CLAUDE_CODE_SESSION_ID)
    task_file          TEXT NOT NULL,
    workdir            TEXT NOT NULL,
    sandbox            TEXT,
    model              TEXT,
    effort             TEXT,
    speed              TEXT,
    started_at         TEXT NOT NULL,
    ended_at           TEXT,
    exit_code          INTEGER,
    rollout_id         TEXT,               -- matched codex_sessions row
    ephemeral          INTEGER NOT NULL DEFAULT 0,
    usage_json         TEXT,               -- ephemeral Codex exec's native turn.completed usage
    runtime            TEXT NOT NULL DEFAULT 'codex',
    crew_session_id    TEXT,
    crew_execution_id  TEXT,
    pid                INTEGER,
    pid_created        TEXT,
    native_id          TEXT
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

-- One row per Claude weekly quota window seen. claude-limits.json keeps only the newest
-- window, so past weeks' account percentages live here; Crew's share is costed on read.
CREATE TABLE IF NOT EXISTS claude_weeks (
    resets_at     TEXT PRIMARY KEY,    -- window end, rounded to the minute (sources differ in sub-seconds)
    since         TEXT NOT NULL,       -- resets_at minus 7 days
    used_percent  REAL,                -- last account percentage seen in this window; NULL = never seen
    captured_at   TEXT
);

-- A Crew identity can return to each runtime's own native history/options.
CREATE TABLE IF NOT EXISTS session_runtime_bindings (
 session_id TEXT NOT NULL REFERENCES sessions(session_id),
 runtime TEXT NOT NULL CHECK(runtime IN ('claude','codex')),
 native_id TEXT,
 model TEXT,
 effort TEXT,
 updated_at TEXT NOT NULL,
 speed TEXT,
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
 last_error TEXT,
 target_speed TEXT
);
CREATE INDEX IF NOT EXISTS runtime_switches_session ON runtime_switches(session_id,status,configured_at);

-- Leader-scoped trust.
CREATE TABLE IF NOT EXISTS leader_trust_settings (
 leader_session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
 project TEXT NOT NULL REFERENCES projects(name),
 enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
 goal TEXT,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decision_leader_scopes (
 decision_id INTEGER PRIMARY KEY REFERENCES decisions(id),
 leader_session_id TEXT REFERENCES sessions(session_id)
);

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
CREATE TABLE IF NOT EXISTS general_goal_handoffs (
 session_id TEXT PRIMARY KEY REFERENCES sessions(session_id) ON DELETE CASCADE,
 goal_id TEXT NOT NULL REFERENCES goals(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS goal_executions (
 id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, round INTEGER NOT NULL,
 session_id TEXT NOT NULL REFERENCES sessions(session_id), generation INTEGER NOT NULL,
 runtime TEXT NOT NULL CHECK(runtime IN ('claude','codex')), model TEXT, effort TEXT,
 status TEXT NOT NULL, requirement_version INTEGER NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, ended_at TEXT, error TEXT, speed TEXT,
 FOREIGN KEY(goal_id,round) REFERENCES goal_rounds(goal_id,number),
 UNIQUE(goal_id,round,session_id,generation)
);
CREATE INDEX IF NOT EXISTS goal_executions_goal ON goal_executions(goal_id,round,status);
CREATE TABLE IF NOT EXISTS goal_execution_bindings (
 execution_id TEXT NOT NULL REFERENCES goal_executions(id),
 runtime TEXT NOT NULL CHECK(runtime IN ('claude','codex')), native_id TEXT,
 model TEXT, effort TEXT, updated_at TEXT NOT NULL, speed TEXT,
 PRIMARY KEY(execution_id,runtime), UNIQUE(runtime,native_id)
);
-- The identity belongs to the native history, including after a carrier switch.
CREATE TABLE IF NOT EXISTS native_binding_origins (
 runtime TEXT NOT NULL CHECK(runtime IN ('claude','codex')),
 native_id TEXT NOT NULL,
 origin TEXT NOT NULL CHECK(origin IN ('crew_created','user_adopted')),
 archive_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(archive_confirmed IN (0,1)),
 PRIMARY KEY(runtime,native_id)
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
 last_attempt_at TEXT, accepted_at TEXT, error TEXT,
 target_scope TEXT NOT NULL DEFAULT 'goal' CHECK(target_scope IN ('goal','general'))
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
 delivered_version INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(kind,item_id,execution_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS goal_decision_single_link ON goal_links(kind,item_id) WHERE kind='decision';
CREATE TABLE IF NOT EXISTS goal_cancellations (
 goal_id TEXT PRIMARY KEY REFERENCES goals(id), round INTEGER NOT NULL,
 requested_at TEXT NOT NULL, actor TEXT NOT NULL
);
-- Handing a task to another leader waits for running turns to end; finished rows stay as history.
CREATE TABLE IF NOT EXISTS goal_transfers (
 id TEXT PRIMARY KEY, goal_id TEXT NOT NULL REFERENCES goals(id), round INTEGER NOT NULL,
 from_session_id TEXT NOT NULL REFERENCES sessions(session_id),
 to_session_id TEXT NOT NULL REFERENCES sessions(session_id),
 note TEXT NOT NULL, actor TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','finalized','failed')), error TEXT,
 requested_at TEXT NOT NULL, finalized_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS goal_transfers_pending ON goal_transfers(goal_id) WHERE status='pending';
CREATE TABLE IF NOT EXISTS session_stop_gates (
 session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
 stopped_at TEXT NOT NULL, stopped_by TEXT NOT NULL
);

-- Documents belong to explicitly recorded tasks, never inferred from a leader.
-- One original file may be delivered in more than one task.
CREATE TABLE IF NOT EXISTS goal_documents (
 goal_id TEXT NOT NULL REFERENCES goals(id), document_id INTEGER NOT NULL REFERENCES documents(id),
 execution_id TEXT REFERENCES goal_executions(id), created_at TEXT NOT NULL,
 PRIMARY KEY(goal_id,document_id)
);
CREATE TABLE IF NOT EXISTS goal_acceptances (
 goal_id TEXT NOT NULL REFERENCES goals(id), round INTEGER NOT NULL,
 report_id TEXT NOT NULL REFERENCES goal_reports(id),
 accepted INTEGER NOT NULL CHECK(accepted IN (0,1)), note TEXT NOT NULL,
 actor TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(goal_id,round)
);

-- "观察中": the user has looked at this delivery and is fine so far, but keeps the task open to watch it
-- in real use before deciding. The task stays awaiting_acceptance; nothing runs and no AI is woken.
CREATE TABLE IF NOT EXISTS goal_observations (
 goal_id TEXT NOT NULL REFERENCES goals(id), report_id TEXT NOT NULL REFERENCES goal_reports(id),
 note TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(goal_id,report_id)
);

-- Continuing an existing delivery preserves its previous finish and review.
-- A new execution/requirement version does not imply a new user-visible round.
CREATE TABLE IF NOT EXISTS goal_continuations (
 id TEXT PRIMARY KEY, goal_id TEXT NOT NULL REFERENCES goals(id), round INTEGER NOT NULL,
 requirement_version INTEGER NOT NULL, report_id TEXT REFERENCES goal_reports(id),
 previous_status TEXT NOT NULL, reason TEXT NOT NULL, actor TEXT NOT NULL,
 review_json TEXT, finish_json TEXT, round_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS goal_discussion_executions (
 execution_id TEXT PRIMARY KEY REFERENCES goal_executions(id),
 message_id TEXT NOT NULL, created_at TEXT NOT NULL, promoted_at TEXT, promotion_json TEXT
);

-- Workspace labels and visibility never change the stable projects.name key.
CREATE TABLE IF NOT EXISTS project_settings (
 project TEXT PRIMARY KEY REFERENCES projects(name),
 display_name TEXT, removed_at TEXT,
 revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0)
);
CREATE TABLE IF NOT EXISTS project_directory_changes (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 project TEXT NOT NULL REFERENCES projects(name),
 revision INTEGER NOT NULL,
 old_root TEXT NOT NULL, new_root TEXT NOT NULL,
 session_paths_json TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(project,revision)
);

-- Department compaction and current-workspace cleanup. Results survive page
-- reloads; a compaction item completes only on a native compact event.
CREATE TABLE IF NOT EXISTS maintenance_jobs (
 id TEXT PRIMARY KEY,
 project TEXT NOT NULL REFERENCES projects(name),
 kind TEXT NOT NULL CHECK(kind IN ('department_compact','workspace_cleanup')),
 leader_session_id TEXT REFERENCES sessions(session_id),
 request_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','completed','partial','failed','interrupted')),
 error_type TEXT, error TEXT,
 detail_json TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, finished_at TEXT,
 UNIQUE(project,kind,request_id)
);
CREATE INDEX IF NOT EXISTS maintenance_jobs_project ON maintenance_jobs(project,kind,created_at);
CREATE TABLE IF NOT EXISTS maintenance_items (
 job_id TEXT NOT NULL REFERENCES maintenance_jobs(id),
 position INTEGER NOT NULL,
 target TEXT NOT NULL,
 session_id TEXT REFERENCES sessions(session_id),
 execution_id TEXT REFERENCES goal_executions(id),
 label TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','running','completed','failed','interrupted')),
 error_type TEXT, error TEXT,
 detail_json TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 PRIMARY KEY(job_id,position)
);
-- Monotonic per-workspace boundaries: older channel posts, digests and
-- handoffs stay stored and readable as history, but are never shown in the
-- live channel or delivered to sessions created after a cleanup.
CREATE TABLE IF NOT EXISTS workspace_cleanup_boundaries (
 project TEXT PRIMARY KEY REFERENCES projects(name),
 channel_after INTEGER NOT NULL CHECK(channel_after>=0),
 digest_after INTEGER NOT NULL CHECK(digest_after>=0),
 handoff_after INTEGER NOT NULL CHECK(handoff_after>=0),
 cleanups INTEGER NOT NULL CHECK(cleanups>=0),
 job_id TEXT NOT NULL REFERENCES maintenance_jobs(id),
 updated_at TEXT NOT NULL
);

-- Native desktop handoffs retain exact identity and storage, including across
-- a Crew restart. Transitional states also block automatic model work.
CREATE TABLE IF NOT EXISTS native_session_controls (
 scope_id TEXT PRIMARY KEY,
 session_id TEXT NOT NULL REFERENCES sessions(session_id),
 execution_id TEXT REFERENCES goal_executions(id),
 native_id TEXT NOT NULL,
 native_home TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('crew','releasing','external','returning')),
 token TEXT,
 last_error TEXT,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS goal_work_items (
    id TEXT PRIMARY KEY,
    goal_id TEXT NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    round INTEGER NOT NULL,
    requirement_version INTEGER NOT NULL,
    key TEXT NOT NULL,
    title TEXT NOT NULL,
    acceptance TEXT NOT NULL,
    owner_session_id TEXT NOT NULL REFERENCES sessions(session_id),
    dependencies_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('planned','doing','blocked','done','cancelled')),
    note TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(goal_id,round,key)
);
CREATE TABLE IF NOT EXISTS goal_work_history (
    item_id TEXT NOT NULL REFERENCES goal_work_items(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    actor TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(item_id,version)
);

-- Architecture board: each workspace's domain tree. A part nests under one parent part (NULL = top level); any
-- number of leaders work in it and one of them may lead it. Positions and collapse are the board layout.
CREATE TABLE IF NOT EXISTS domains (
    id         TEXT PRIMARY KEY,
    project    TEXT NOT NULL REFERENCES projects(name),
    parent_id  TEXT REFERENCES domains(id),
    name       TEXT NOT NULL,
    x          REAL NOT NULL DEFAULT 0,
    y          REAL NOT NULL DEFAULT 0,
    collapsed  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS domains_project ON domains (project, parent_id);
-- The part a leader works in (at most one; NULL = unassigned). lead=1 marks the one leading that part, owning
-- the work its sub-parts share (unique per part, see store migration). An archived leader keeps its place.
-- Unassigned leaders can use x/y to pin their card on the board.
CREATE TABLE IF NOT EXISTS domain_leaders (
    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
    project    TEXT NOT NULL REFERENCES projects(name),
    domain_id  TEXT REFERENCES domains(id),
    x          REAL,
    y          REAL,
    updated_at TEXT NOT NULL,
    lead       INTEGER NOT NULL DEFAULT 0
);
-- Bumped by every board edit, so a page holding an older copy (and its undo steps) knows to reload first.
-- direction: TB draws the tree top-down, LR left-to-right like a blueprint.
CREATE TABLE IF NOT EXISTS domain_boards (
    project    TEXT PRIMARY KEY REFERENCES projects(name),
    version    INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    direction  TEXT NOT NULL DEFAULT 'TB' CHECK (direction IN ('TB', 'LR'))
);
-- The board's structure after each batch that changed it (docs/ASSISTANT_CAMPAIGN_DESIGN.md §3). number counts
-- from 1 per workspace; baseline is the board as it was before the first recorded change. state holds parts and
-- placed leaders; detail explains a restructure or rollback. A rollback is a version of its own (restored_from).
CREATE TABLE IF NOT EXISTS domain_board_versions (
    project       TEXT NOT NULL REFERENCES projects(name),
    number        INTEGER NOT NULL,
    board_version INTEGER NOT NULL,
    source        TEXT NOT NULL CHECK (source IN ('baseline', 'edit', 'mindmap', 'restructure', 'rollback')),
    note          TEXT NOT NULL,
    state         TEXT NOT NULL,
    detail        TEXT,
    restored_from INTEGER,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (project, number)
);

-- Operation receipts: a write retried with the same request_id runs once (OPERATIONS_REGISTRY_DESIGN §5.5).
CREATE TABLE IF NOT EXISTS op_requests (
 caller_key TEXT NOT NULL,
 request_id TEXT NOT NULL,
 op TEXT NOT NULL,
 args_hash TEXT NOT NULL,
 status TEXT NOT NULL CHECK (status IN ('running','done','failed','unknown')),
 result TEXT,
 created_at TEXT NOT NULL,
 finished_at TEXT,
 PRIMARY KEY (caller_key, request_id)
);

-- Per-session MCP tokens; only the hash is stored (OPERATIONS_REGISTRY_DESIGN §3.2).
CREATE TABLE IF NOT EXISTS mcp_tokens (
 token_hash TEXT PRIMARY KEY,
 session_id TEXT NOT NULL,
 execution_id TEXT,
 goal_id TEXT,
 role TEXT NOT NULL,
 runtime TEXT NOT NULL,
 project TEXT,
 own_scopes TEXT NOT NULL,
 delegable_scopes TEXT NOT NULL,
 issuer_token_hash TEXT,
 created_at TEXT NOT NULL,
 revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS mcp_tokens_session ON mcp_tokens(session_id, revoked_at);

-- Tools a Claude conversation found with crew_search (execution_id '' = its general conversation).
-- Kept per conversation, not per token, so a resumed native still lists them after a restart.
CREATE TABLE IF NOT EXISTS mcp_loaded_tools (
 session_id TEXT NOT NULL,
 execution_id TEXT NOT NULL,
 op TEXT NOT NULL,
 PRIMARY KEY (session_id, execution_id, op)
);

-- User confirmations of high-risk operations an AI asked for (OPERATIONS_REGISTRY_DESIGN §5.6).
CREATE TABLE IF NOT EXISTS interaction_requests (
 id TEXT PRIMARY KEY,
 kind TEXT NOT NULL CHECK (kind IN ('confirmation')),
 caller_key TEXT NOT NULL,
 request_id TEXT NOT NULL,
 session_id TEXT NOT NULL,
 execution_id TEXT,
 role TEXT NOT NULL,
 op TEXT NOT NULL,
 args TEXT NOT NULL,
 args_hash TEXT NOT NULL,
 project TEXT,
 status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','failed')),
 result TEXT,
 error TEXT,
 created_at TEXT NOT NULL,
 decided_at TEXT,
 UNIQUE (caller_key, request_id)
);

-- BEGIN workspace_notes_v1
CREATE TABLE IF NOT EXISTS schema_migrations(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS personal_assistant (singleton INTEGER PRIMARY KEY CHECK(singleton=1), session_id TEXT NOT NULL UNIQUE REFERENCES sessions(session_id));

CREATE TABLE IF NOT EXISTS personal_notes (id TEXT PRIMARY KEY, project TEXT REFERENCES projects(name), original_text TEXT NOT NULL, text TEXT NOT NULL, tags_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open' CHECK(state IN ('open','archived')), version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, source_json TEXT NOT NULL DEFAULT '{}', title TEXT, pinned_at TEXT, snoozed_until TEXT, merged_into TEXT, stage TEXT NOT NULL DEFAULT 'new' CHECK(stage IN ('new','discussed','promoted')), last_discussed_at TEXT, reviewed_at TEXT, summary_json TEXT NOT NULL DEFAULT '[]');

CREATE TABLE IF NOT EXISTS personal_note_discussions (note_id TEXT NOT NULL REFERENCES personal_notes(id), session_id TEXT NOT NULL REFERENCES sessions(session_id), message_id TEXT NOT NULL, seq INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(session_id, message_id));

CREATE INDEX IF NOT EXISTS personal_note_discussions_note ON personal_note_discussions(note_id, created_at);

CREATE TABLE IF NOT EXISTS personal_note_requests (request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response_json TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS personal_note_leaders (note_id TEXT PRIMARY KEY REFERENCES personal_notes(id), project TEXT NOT NULL REFERENCES projects(name), request_id TEXT NOT NULL UNIQUE, source_json TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','completed','unknown')), session_id TEXT REFERENCES sessions(session_id), error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS personal_action_receipts (request_id TEXT PRIMARY KEY, action TEXT NOT NULL, fingerprint TEXT NOT NULL, instruction_json TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','completed','failed','unknown')), response_json TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE INDEX IF NOT EXISTS personal_action_fingerprint ON personal_action_receipts(fingerprint);

CREATE TABLE IF NOT EXISTS personal_mindmap_nodes (id TEXT PRIMARY KEY, parent_id TEXT REFERENCES personal_mindmap_nodes(id), note_id TEXT UNIQUE REFERENCES personal_notes(id), label TEXT, sort_order INTEGER NOT NULL, collapsed INTEGER NOT NULL DEFAULT 0 CHECK(collapsed IN (0,1)), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, project TEXT REFERENCES projects(name), x REAL, y REAL, CHECK((note_id IS NULL) <> (label IS NULL)));

CREATE TABLE IF NOT EXISTS personal_mindmap_links (node_id TEXT NOT NULL REFERENCES personal_mindmap_nodes(id), project TEXT NOT NULL REFERENCES projects(name), domain_id TEXT NOT NULL, converted_at TEXT NOT NULL, PRIMARY KEY(node_id, project));

CREATE TABLE IF NOT EXISTS workspace_note_tags (
    project TEXT NOT NULL REFERENCES projects(name),
    name TEXT NOT NULL,
    color TEXT NOT NULL CHECK(color IN ('c0','c1','c2','c3','c4','c5','c6','c7','c8','c9')),
    pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0,1)),
    sort_order INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(project,name)
);

CREATE TABLE IF NOT EXISTS unassigned_note_tags (
    name TEXT PRIMARY KEY,
    color TEXT NOT NULL CHECK(color IN ('c0','c1','c2','c3','c4','c5','c6','c7','c8','c9')),
    pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0,1)),
    sort_order INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workspace_mindmaps (
    project TEXT PRIMARY KEY NOT NULL REFERENCES projects(name),
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unassigned_pool (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    version INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS workspace_mindmap_parent ON personal_mindmap_nodes(project,parent_id,sort_order);

CREATE TRIGGER IF NOT EXISTS mindmap_scope_insert BEFORE INSERT ON personal_mindmap_nodes
WHEN (NEW.parent_id IS NOT NULL AND NEW.project IS NOT
         (SELECT project FROM personal_mindmap_nodes WHERE id=NEW.parent_id))
  OR (NEW.note_id IS NOT NULL AND NEW.project IS NOT
         (SELECT project FROM personal_notes WHERE id=NEW.note_id))
  OR EXISTS (SELECT 1 FROM personal_mindmap_nodes WHERE parent_id=NEW.id AND project IS NOT NEW.project)
BEGIN SELECT RAISE(ABORT, 'mindmap project mismatch'); END;

CREATE TRIGGER IF NOT EXISTS mindmap_scope_update BEFORE UPDATE OF project,parent_id,note_id ON personal_mindmap_nodes
WHEN (NEW.parent_id IS NOT NULL AND NEW.project IS NOT
         (SELECT project FROM personal_mindmap_nodes WHERE id=NEW.parent_id))
  OR (NEW.note_id IS NOT NULL AND NEW.project IS NOT
         (SELECT project FROM personal_notes WHERE id=NEW.note_id))
  OR EXISTS (SELECT 1 FROM personal_mindmap_nodes WHERE parent_id=NEW.id AND project IS NOT NEW.project)
BEGIN SELECT RAISE(ABORT, 'mindmap project mismatch'); END;

CREATE TRIGGER IF NOT EXISTS mindmap_note_scope BEFORE UPDATE OF project ON personal_notes
WHEN EXISTS (SELECT 1 FROM personal_mindmap_nodes WHERE note_id=NEW.id AND project IS NOT NEW.project)
BEGIN SELECT RAISE(ABORT, 'mindmap note project mismatch'); END;
-- END workspace_notes_v1

-- Imported discussions whose original chat/message did not travel. No local jump or grant.
CREATE TABLE IF NOT EXISTS imported_note_discussion_history (
    id TEXT PRIMARY KEY,
    note_id TEXT NOT NULL REFERENCES personal_notes(id),
    source_kind TEXT NOT NULL CHECK(source_kind IN ('workspace','administrator','external')),
    created_at TEXT NOT NULL
);
