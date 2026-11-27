CREATE TABLE canvas_positions (
    id TEXT PRIMARY KEY CHECK(length(id)=36),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    modified_at TEXT NOT NULL,
    agent_id TEXT, node_id TEXT, x REAL NOT NULL, y REAL NOT NULL,
    CHECK ((agent_id IS NULL) <> (node_id IS NULL)),
    UNIQUE(workspace_id,id), UNIQUE(workspace_id,agent_id), UNIQUE(workspace_id,node_id),
    FOREIGN KEY(workspace_id,agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(workspace_id,node_id) REFERENCES mindmap_nodes(workspace_id,id) DEFERRABLE INITIALLY DEFERRED
);
