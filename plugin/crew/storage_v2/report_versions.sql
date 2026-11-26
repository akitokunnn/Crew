-- Old reports carry only a format marker. Format 2 stores the exact runtime row.
CREATE TABLE goal_report_versions (
    id TEXT PRIMARY KEY CHECK(length(id)=36),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    report_id TEXT NOT NULL UNIQUE,
    format_version INTEGER NOT NULL CHECK(format_version IN (1,2)),
    version INTEGER,
    requirement_version INTEGER,
    created_at TEXT NOT NULL,
    CHECK ((format_version=1 AND version IS NULL AND requirement_version IS NULL)
        OR (format_version=2 AND typeof(version)='integer' AND version>=1
            AND typeof(requirement_version)='integer' AND requirement_version>=1)),
    FOREIGN KEY(workspace_id,report_id) REFERENCES reports(workspace_id,id) DEFERRABLE INITIALLY DEFERRED
);
