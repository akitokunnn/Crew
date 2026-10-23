-- Crew board schema. One database (~/.crew/crew.db) shared by every project.
-- Times are UTC ISO-8601 strings with millisecond precision and a trailing Z.
-- Owned by the main controller; tasks extend it only through docs/DESIGN.md.

PRAGMA user_version = 1;

-- ---------------------------------------------------------------- board

CREATE TABLE IF NOT EXISTS projects (
    name        TEXT PRIMARY KEY,
    root        TEXT NOT NULL UNIQUE,      -- absolute path, as given (compare with crew.store.norm_path)
    created_at  TEXT NOT NULL
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

CREATE TABLE IF NOT EXISTS git_management (
    project TEXT PRIMARY KEY REFERENCES projects(name),
    enabled_at TEXT NOT NULL,
    base TEXT NOT NULL,
    stable TEXT,
    admin_session_id TEXT REFERENCES sessions(session_id),
    branch_rules TEXT
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
