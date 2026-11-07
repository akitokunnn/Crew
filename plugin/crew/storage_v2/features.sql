-- Workspace-owned V2 feature state. IDs and ownership use typed foreign keys;
-- payloads are allowlisted, versioned domain fields, never executable grants.
CREATE TABLE IF NOT EXISTS workspace_feature_records (
 id TEXT PRIMARY KEY CHECK(length(id)=36),
 workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
 revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 deleted_at TEXT,
 kind TEXT NOT NULL,
 format_version INTEGER NOT NULL DEFAULT 1 CHECK(format_version=1),
 owner_agent_id TEXT,
 goal_id TEXT,
 execution_id TEXT,
 todo_id TEXT,
 decision_id TEXT,
 report_id TEXT,
 parent_record_id TEXT,
 blob_id TEXT,
 baseline_blob_id TEXT,
 data_json TEXT NOT NULL CHECK(json_valid(data_json)),
 UNIQUE(workspace_id,id),
 FOREIGN KEY(workspace_id,owner_agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(workspace_id,goal_id) REFERENCES goals(workspace_id,id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(workspace_id,execution_id) REFERENCES executions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(workspace_id,todo_id) REFERENCES todos(workspace_id,id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(workspace_id,decision_id) REFERENCES decisions(workspace_id,id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(workspace_id,report_id) REFERENCES reports(workspace_id,id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(workspace_id,parent_record_id) REFERENCES workspace_feature_records(workspace_id,id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(workspace_id,blob_id) REFERENCES blob_objects(workspace_id,id) DEFERRABLE INITIALLY DEFERRED,
 FOREIGN KEY(workspace_id,baseline_blob_id) REFERENCES blob_objects(workspace_id,id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX IF NOT EXISTS workspace_feature_kind ON workspace_feature_records(workspace_id,kind);
CREATE TRIGGER IF NOT EXISTS guard_workspace_feature_identity
BEFORE UPDATE ON workspace_feature_records
WHEN NEW.id IS NOT OLD.id OR NEW.workspace_id IS NOT OLD.workspace_id
 OR NEW.created_at IS NOT OLD.created_at OR NEW.revision<=OLD.revision
BEGIN SELECT RAISE(ABORT,'workspace feature identity is fixed and revision must advance'); END;
