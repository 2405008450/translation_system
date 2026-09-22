BEGIN;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS review_sync_enabled BOOLEAN NOT NULL DEFAULT TRUE;

DO $$
BEGIN
    LOCK TABLE projects IN ACCESS EXCLUSIVE MODE;
    IF COALESCE((
        SELECT pg_get_expr(d.adbin, d.adrelid)
        FROM pg_attribute a
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid = 'projects'::regclass AND a.attname = 'review_sync_enabled'
    ), '') <> 'true' THEN
        ALTER TABLE projects ALTER COLUMN review_sync_enabled SET DEFAULT TRUE;
        UPDATE projects SET review_sync_enabled = TRUE WHERE review_sync_enabled = FALSE;
    END IF;
END $$
;

CREATE TABLE IF NOT EXISTS review_sync_groups (
	id UUID NOT NULL, 
	project_id UUID NOT NULL, 
	source_segment_id UUID NOT NULL, 
	author_id UUID NOT NULL, 
	source_hash VARCHAR(128) NOT NULL, 
	source_language VARCHAR(20) NOT NULL, 
	target_language VARCHAR(20) NOT NULL, 
	before_text TEXT NOT NULL, 
	after_text TEXT NOT NULL, 
	source_version INTEGER NOT NULL, 
	generation INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
	FOREIGN KEY(source_segment_id) REFERENCES segments (id) ON DELETE CASCADE, 
	FOREIGN KEY(author_id) REFERENCES users (id) ON DELETE CASCADE
)

;
CREATE INDEX IF NOT EXISTS ix_review_sync_groups_project_id ON review_sync_groups (project_id);
CREATE INDEX IF NOT EXISTS ix_review_sync_groups_source_segment_id ON review_sync_groups (source_segment_id);

CREATE TABLE IF NOT EXISTS review_sync_members (
	id UUID NOT NULL, 
	group_id UUID NOT NULL, 
	segment_id UUID NOT NULL, 
	revision_id UUID, 
	before_text TEXT NOT NULL, 
	after_text TEXT NOT NULL, 
	version INTEGER NOT NULL, 
	active BOOLEAN NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (group_id, segment_id), 
	FOREIGN KEY(group_id) REFERENCES review_sync_groups (id) ON DELETE CASCADE, 
	FOREIGN KEY(segment_id) REFERENCES segments (id) ON DELETE CASCADE, 
	FOREIGN KEY(revision_id) REFERENCES segment_revisions (id) ON DELETE SET NULL
)

;
CREATE INDEX IF NOT EXISTS ix_review_sync_members_group_id ON review_sync_members (group_id);
CREATE INDEX IF NOT EXISTS ix_review_sync_members_revision_id ON review_sync_members (revision_id);
CREATE INDEX IF NOT EXISTS ix_review_sync_members_segment_id ON review_sync_members (segment_id);

CREATE TABLE IF NOT EXISTS review_sync_tasks (
	id UUID NOT NULL, 
	group_id UUID NOT NULL, 
	generation INTEGER NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	attempts INTEGER NOT NULL, 
	result JSON NOT NULL, 
	error TEXT NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (group_id), 
	FOREIGN KEY(group_id) REFERENCES review_sync_groups (id) ON DELETE CASCADE
)

;
CREATE INDEX IF NOT EXISTS ix_review_sync_tasks_status ON review_sync_tasks (status);
COMMIT;
