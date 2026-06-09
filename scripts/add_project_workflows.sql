CREATE TABLE IF NOT EXISTS project_workflow_steps (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    step_key VARCHAR(40) NOT NULL,
    name VARCHAR(80) NOT NULL,
    step_type VARCHAR(20) NOT NULL DEFAULT 'custom',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE IF EXISTS project_workflow_steps
    ADD COLUMN IF NOT EXISTS step_key VARCHAR(40) NOT NULL DEFAULT 'translate';
ALTER TABLE IF EXISTS project_workflow_steps
    ADD COLUMN IF NOT EXISTS name VARCHAR(80) NOT NULL DEFAULT '翻译';
ALTER TABLE IF EXISTS project_workflow_steps
    ADD COLUMN IF NOT EXISTS step_type VARCHAR(20) NOT NULL DEFAULT 'custom';
ALTER TABLE IF EXISTS project_workflow_steps
    ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS project_workflow_steps
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW();

CREATE INDEX IF NOT EXISTS ix_project_workflow_steps_project_id
    ON project_workflow_steps (project_id);
CREATE INDEX IF NOT EXISTS ix_project_workflow_steps_project_order
    ON project_workflow_steps (project_id, sort_order);

INSERT INTO project_workflow_steps (project_id, step_key, name, step_type, sort_order)
SELECT p.id, 'translate', '翻译', 'translation', 0
FROM projects AS p
WHERE NOT EXISTS (
    SELECT 1
    FROM project_workflow_steps AS pws
    WHERE pws.project_id = p.id
);

ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS workflow_step_id UUID REFERENCES project_workflow_steps(id) ON DELETE SET NULL;

ALTER TABLE IF EXISTS file_assignments
    ADD COLUMN IF NOT EXISTS workflow_step_id UUID REFERENCES project_workflow_steps(id) ON DELETE CASCADE;

UPDATE segments AS s
SET workflow_step_id = first_step.id
FROM file_records AS fr
JOIN (
    SELECT DISTINCT ON (project_id)
        id,
        project_id
    FROM project_workflow_steps
    ORDER BY project_id, sort_order ASC, id ASC
) AS first_step ON first_step.project_id = fr.project_id
WHERE s.workflow_step_id IS NULL
  AND s.file_record_id = fr.id;

UPDATE file_assignments AS fa
SET workflow_step_id = first_step.id
FROM (
    SELECT DISTINCT ON (project_id)
        id,
        project_id
    FROM project_workflow_steps
    ORDER BY project_id, sort_order ASC, id ASC
) AS first_step
WHERE fa.workflow_step_id IS NULL
  AND fa.project_id = first_step.project_id;

DROP INDEX IF EXISTS uq_file_assignments_active_file_user;

CREATE UNIQUE INDEX IF NOT EXISTS uq_file_assignments_active_file_step_user
    ON file_assignments (file_record_id, workflow_step_id, assignee_id)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS ix_segments_workflow_step_id
    ON segments (workflow_step_id);
