-- 为项目管理增加 projects 父表，并把既有 file_records 回填为一项目一文件
-- 运行方式: psql -U <user> -d <database> -f scripts/add_project_parent_tables.sql

BEGIN;

CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    name VARCHAR(200) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    source_language VARCHAR(20),
    target_language VARCHAR(20),
    creator_id UUID REFERENCES users(id) ON DELETE SET NULL,
    deadline TIMESTAMP,
    access_level VARCHAR(20) NOT NULL DEFAULT 'team',
    auto_tm_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE file_records ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE CASCADE;
ALTER TABLE file_records ADD COLUMN IF NOT EXISTS source_language VARCHAR(20);
ALTER TABLE file_records ADD COLUMN IF NOT EXISTS target_language VARCHAR(20);
ALTER TABLE file_records ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE file_records ADD COLUMN IF NOT EXISTS collection_id UUID REFERENCES memory_bases(id) ON DELETE SET NULL;
ALTER TABLE file_records ADD COLUMN IF NOT EXISTS term_base_id UUID REFERENCES term_bases(id) ON DELETE SET NULL;
ALTER TABLE file_records ADD COLUMN IF NOT EXISTS deadline TIMESTAMP;
ALTER TABLE file_records ADD COLUMN IF NOT EXISTS access_level VARCHAR(20) NOT NULL DEFAULT 'team';
ALTER TABLE projects ADD COLUMN IF NOT EXISTS auto_tm_enabled BOOLEAN NOT NULL DEFAULT TRUE;

CREATE INDEX IF NOT EXISTS ix_projects_creator_id ON projects(creator_id);
CREATE INDEX IF NOT EXISTS ix_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS ix_file_records_project_id ON file_records(project_id);
CREATE INDEX IF NOT EXISTS ix_file_records_creator_id ON file_records(creator_id);
CREATE INDEX IF NOT EXISTS ix_file_records_source_language ON file_records(source_language);
CREATE INDEX IF NOT EXISTS ix_file_records_status ON file_records(status);

INSERT INTO projects (
    id,
    name,
    status,
    source_language,
    target_language,
    creator_id,
    deadline,
    access_level,
    created_at,
    updated_at
)
SELECT
    fr.id,
    fr.filename,
    fr.status,
    fr.source_language,
    fr.target_language,
    fr.creator_id,
    fr.deadline,
    COALESCE(NULLIF(fr.access_level, ''), 'team'),
    fr.created_at,
    fr.updated_at
FROM file_records AS fr
WHERE fr.project_id IS NULL
ON CONFLICT (id) DO NOTHING;

UPDATE file_records AS fr
SET project_id = fr.id
WHERE fr.project_id IS NULL
  AND EXISTS (
      SELECT 1
      FROM projects AS p
      WHERE p.id = fr.id
  );

COMMIT;
