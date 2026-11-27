-- Additive portable schema 5. Composite references keep every edge in its workspace.

CREATE TABLE note_tag_settings (
    id TEXT PRIMARY KEY CHECK(length(id)=36),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    name TEXT NOT NULL,
    color TEXT NOT NULL CHECK(color IN ('c0','c1','c2','c3','c4','c5','c6','c7','c8','c9')),
    pinned INTEGER NOT NULL CHECK(pinned IN (0,1)),
    sort_order INTEGER NOT NULL,
    version INTEGER NOT NULL,
    modified_at TEXT NOT NULL,
    UNIQUE(workspace_id,id),
    UNIQUE(workspace_id,name)
);

CREATE TABLE mindmaps (
    id TEXT PRIMARY KEY CHECK(length(id)=36),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version>=0),
    modified_at TEXT NOT NULL,
    UNIQUE(workspace_id,id),
    UNIQUE(workspace_id)
);

CREATE TABLE mindmap_nodes (
    id TEXT PRIMARY KEY CHECK(length(id)=36),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    map_id TEXT NOT NULL,
    parent_id TEXT,
    note_id TEXT,
    label TEXT,
    sort_order INTEGER NOT NULL,
    collapsed INTEGER NOT NULL CHECK(collapsed IN (0,1)),
    modified_at TEXT NOT NULL,
    UNIQUE(workspace_id,id),
    CHECK((note_id IS NULL) <> (label IS NULL)),
    FOREIGN KEY(workspace_id,map_id) REFERENCES mindmaps(workspace_id,id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(workspace_id,parent_id) REFERENCES mindmap_nodes(workspace_id,id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(workspace_id,note_id) REFERENCES workspace_feature_records(workspace_id,id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE mindmap_domains (
    id TEXT PRIMARY KEY CHECK(length(id)=36),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    parent_id TEXT,
    name TEXT NOT NULL,
    x REAL NOT NULL,
    y REAL NOT NULL,
    collapsed INTEGER NOT NULL CHECK(collapsed IN (0,1)),
    modified_at TEXT NOT NULL,
    UNIQUE(workspace_id,id),
    FOREIGN KEY(workspace_id,parent_id) REFERENCES mindmap_domains(workspace_id,id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE mindmap_conversions (
    id TEXT PRIMARY KEY CHECK(length(id)=36),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    node_id TEXT NOT NULL,
    domain_id TEXT NOT NULL,
    UNIQUE(workspace_id,id),
    UNIQUE(workspace_id,node_id),
    FOREIGN KEY(workspace_id,node_id) REFERENCES mindmap_nodes(workspace_id,id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(workspace_id,domain_id) REFERENCES mindmap_domains(workspace_id,id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE workspace_assistant_agents (
    id TEXT PRIMARY KEY CHECK(length(id)=36),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    UNIQUE(workspace_id,id),
    UNIQUE(workspace_id),
    FOREIGN KEY(workspace_id,agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE note_discussions (
    id TEXT PRIMARY KEY CHECK(length(id)=36),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) DEFERRABLE INITIALLY DEFERRED,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    note_id TEXT NOT NULL,
    agent_id TEXT,
    message_id TEXT,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('workspace','administrator','external')),
    UNIQUE(workspace_id,id),
    CHECK((agent_id IS NULL) = (message_id IS NULL)),
    UNIQUE(workspace_id,message_id),
    CHECK(agent_id IS NULL OR source_kind='workspace'),
    FOREIGN KEY(workspace_id,note_id) REFERENCES workspace_feature_records(workspace_id,id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(workspace_id,agent_id) REFERENCES agents(workspace_id,id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(workspace_id,message_id) REFERENCES messages(workspace_id,id) DEFERRABLE INITIALLY DEFERRED
);
