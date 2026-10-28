-- Task extensions retain the existing goals/requirements/executions as authority.
CREATE TABLE IF NOT EXISTS task_controls (
 goal_id TEXT PRIMARY KEY REFERENCES goals(id),
 mode TEXT NOT NULL CHECK(mode IN ('manual','automatic')),
 lifecycle TEXT NOT NULL CHECK(lifecycle IN ('active','archived','trashed')),
 authorized INTEGER NOT NULL CHECK(authorized IN (0,1)),
 mode_source_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_workspaces (
 goal_id TEXT NOT NULL REFERENCES goals(id), project TEXT NOT NULL REFERENCES projects(name),
 created_at TEXT NOT NULL, PRIMARY KEY(goal_id,project)
);
CREATE TABLE IF NOT EXISTS task_requests (
 request_id TEXT PRIMARY KEY, action TEXT NOT NULL, digest TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('completed','submitted','failed','unknown')),
 response_json TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_migrations (
 goal_id TEXT PRIMARY KEY REFERENCES goals(id), source_json TEXT NOT NULL,
 migrated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_workspace_references (
 goal_id TEXT NOT NULL REFERENCES goals(id), reference_json TEXT NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(goal_id,reference_json)
);
CREATE TABLE IF NOT EXISTS task_lifecycle_intents (
 goal_id TEXT PRIMARY KEY REFERENCES goals(id), lifecycle TEXT NOT NULL CHECK(lifecycle IN ('archived','trashed')),
 created_at TEXT NOT NULL, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS task_pins (
 goal_id TEXT PRIMARY KEY REFERENCES goals(id), created_at TEXT NOT NULL
);
