-- =============================================================================
-- 补丁：同步 models.py 与数据库之间缺失的字段
-- 运行方式: psql -U <user> -d <database> -f scripts/add_missing_columns.sql
-- 该脚本是幂等的，可在已有库上重复执行。
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- users 琛細鍖哄垎鍐呴儴璇戣€呬笌澶栭儴鍏艰亴璇戣€?
-- -----------------------------------------------------------------------------
ALTER TABLE IF EXISTS users
    ADD COLUMN IF NOT EXISTS translator_type VARCHAR(20) NOT NULL DEFAULT 'internal';

UPDATE users
SET translator_type = 'internal'
WHERE translator_type IS NULL
   OR translator_type NOT IN ('internal', 'external');

-- -----------------------------------------------------------------------------
-- file_records 琛細任务指派负责人
-- -----------------------------------------------------------------------------
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS assignee_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS assigned_by_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS assigned_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS ix_file_records_assignee_id
    ON file_records (assignee_id);

-- -----------------------------------------------------------------------------
-- project-level assignment, file authorization, assignment audit and messages
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS project_assignments (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    assignee_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    assigned_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    assigned_at TIMESTAMP NOT NULL DEFAULT NOW(),
    revoked_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    revoked_at TIMESTAMP,
    status VARCHAR(20) NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS file_assignments (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_record_id UUID NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
    assignee_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    assigned_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    assigned_at TIMESTAMP NOT NULL DEFAULT NOW(),
    revoked_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    revoked_at TIMESTAMP,
    status VARCHAR(20) NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS assignment_events (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_record_id UUID REFERENCES file_records(id) ON DELETE CASCADE,
    assignee_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(40) NOT NULL,
    before_payload TEXT NOT NULL DEFAULT '{}',
    after_payload TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type VARCHAR(40) NOT NULL,
    title VARCHAR(200) NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    file_record_id UUID REFERENCES file_records(id) ON DELETE CASCADE,
    related_event_id UUID REFERENCES assignment_events(id) ON DELETE SET NULL,
    read_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_project_assignments_project_id
    ON project_assignments (project_id);
CREATE INDEX IF NOT EXISTS ix_project_assignments_assignee_id
    ON project_assignments (assignee_id);
CREATE INDEX IF NOT EXISTS ix_project_assignments_status
    ON project_assignments (status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_project_assignments_active_project_user
    ON project_assignments (project_id, assignee_id)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS ix_file_assignments_project_id
    ON file_assignments (project_id);
CREATE INDEX IF NOT EXISTS ix_file_assignments_file_record_id
    ON file_assignments (file_record_id);
CREATE INDEX IF NOT EXISTS ix_file_assignments_assignee_id
    ON file_assignments (assignee_id);
CREATE INDEX IF NOT EXISTS ix_file_assignments_status
    ON file_assignments (status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_file_assignments_active_file_user
    ON file_assignments (file_record_id, assignee_id)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS ix_assignment_events_project_id
    ON assignment_events (project_id);
CREATE INDEX IF NOT EXISTS ix_assignment_events_file_record_id
    ON assignment_events (file_record_id);
CREATE INDEX IF NOT EXISTS ix_assignment_events_assignee_id
    ON assignment_events (assignee_id);
CREATE INDEX IF NOT EXISTS ix_assignment_events_actor_id
    ON assignment_events (actor_id);
CREATE INDEX IF NOT EXISTS ix_assignment_events_created_at
    ON assignment_events (created_at);

CREATE INDEX IF NOT EXISTS ix_notifications_user_id
    ON notifications (user_id);
CREATE INDEX IF NOT EXISTS ix_notifications_read_at
    ON notifications (read_at);
CREATE INDEX IF NOT EXISTS ix_notifications_created_at
    ON notifications (created_at);

INSERT INTO project_assignments (
    project_id,
    assignee_id,
    assigned_by_id,
    assigned_at,
    status
)
SELECT DISTINCT
    fr.project_id,
    fr.assignee_id,
    fr.assigned_by_id,
    COALESCE(fr.assigned_at, NOW()),
    'active'
FROM file_records AS fr
WHERE fr.project_id IS NOT NULL
  AND fr.assignee_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM project_assignments AS pa
      WHERE pa.project_id = fr.project_id
        AND pa.assignee_id = fr.assignee_id
        AND pa.status = 'active'
  );

INSERT INTO file_assignments (
    project_id,
    file_record_id,
    assignee_id,
    assigned_by_id,
    assigned_at,
    status
)
SELECT
    fr.project_id,
    fr.id,
    fr.assignee_id,
    fr.assigned_by_id,
    COALESCE(fr.assigned_at, NOW()),
    'active'
FROM file_records AS fr
WHERE fr.project_id IS NOT NULL
  AND fr.assignee_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM file_assignments AS fa
      WHERE fa.file_record_id = fr.id
        AND fa.assignee_id = fr.assignee_id
        AND fa.status = 'active'
  );

-- -----------------------------------------------------------------------------
-- projects 表：缺少 document_parse_mode
-- -----------------------------------------------------------------------------
ALTER TABLE IF EXISTS projects
    ADD COLUMN IF NOT EXISTS document_parse_mode VARCHAR(20) NOT NULL DEFAULT 'full';

-- -----------------------------------------------------------------------------
-- segments 表：缺少 matched_collection_name / matched_creator_name /
--              matched_created_at / matched_updated_at / LLM 模型信息
-- -----------------------------------------------------------------------------
ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS source_hash VARCHAR(64);
ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS source_html TEXT;
ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS project_sync_disabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS matched_collection_name VARCHAR(120);
ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS matched_creator_name VARCHAR(100);
ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS matched_created_at TIMESTAMP;
ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS matched_updated_at TIMESTAMP;
ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS llm_provider VARCHAR(40);
ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS llm_model VARCHAR(200);

CREATE INDEX IF NOT EXISTS ix_segments_source_hash
    ON segments (source_hash);
CREATE INDEX IF NOT EXISTS ix_segments_file_source_hash
    ON segments (file_record_id, source_hash);

-- -----------------------------------------------------------------------------
-- memory_entries 表：缺少 creator_id
-- -----------------------------------------------------------------------------
ALTER TABLE IF EXISTS memory_entries
    ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS memory_entries
    ADD COLUMN IF NOT EXISTS last_modified_by_id UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_memory_entries_creator_id
    ON memory_entries (creator_id);
CREATE INDEX IF NOT EXISTS ix_memory_entries_last_modified_by_id
    ON memory_entries (last_modified_by_id);

UPDATE memory_entries
SET last_modified_by_id = creator_id
WHERE last_modified_by_id IS NULL
  AND creator_id IS NOT NULL;

-- -----------------------------------------------------------------------------
-- term_entries 表：缺少 creator_id（models.py 中有此字段）
-- -----------------------------------------------------------------------------
ALTER TABLE IF EXISTS term_entries
    ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS term_entries
    ADD COLUMN IF NOT EXISTS last_modified_by_id UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_term_entries_creator_id
    ON term_entries (creator_id);
CREATE INDEX IF NOT EXISTS ix_term_entries_last_modified_by_id
    ON term_entries (last_modified_by_id);

UPDATE term_entries
SET last_modified_by_id = creator_id
WHERE last_modified_by_id IS NULL
  AND creator_id IS NOT NULL;

-- -----------------------------------------------------------------------------
-- document statistics reports：每次点击统计生成一份可切换的字数检验报告
-- -----------------------------------------------------------------------------
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS document_statistics TEXT NOT NULL DEFAULT '{}';

-- -----------------------------------------------------------------------------
-- glossary_bases / glossary_entries：预翻译词汇表
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS glossary_bases (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    name VARCHAR(120) NOT NULL,
    description TEXT,
    source_language VARCHAR(20) NOT NULL,
    target_language VARCHAR(20) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE IF EXISTS glossary_bases
    ADD COLUMN IF NOT EXISTS description TEXT;
ALTER TABLE IF EXISTS glossary_bases
    ADD COLUMN IF NOT EXISTS source_language VARCHAR(20);
ALTER TABLE IF EXISTS glossary_bases
    ADD COLUMN IF NOT EXISTS target_language VARCHAR(20);
ALTER TABLE IF EXISTS glossary_bases
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();
ALTER TABLE IF EXISTS glossary_bases
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS uq_glossary_bases_name
    ON glossary_bases (name);
CREATE INDEX IF NOT EXISTS ix_glossary_bases_language_pair
    ON glossary_bases (source_language, target_language);

DROP TRIGGER IF EXISTS update_glossary_bases_updated_at ON glossary_bases;
CREATE TRIGGER update_glossary_bases_updated_at
    BEFORE UPDATE ON glossary_bases
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS glossary_entries (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    glossary_base_id UUID NOT NULL REFERENCES glossary_bases(id) ON DELETE CASCADE,
    source_text TEXT NOT NULL,
    target_text TEXT NOT NULL,
    note TEXT,
    source_normalized TEXT,
    source_language VARCHAR(20) NOT NULL,
    target_language VARCHAR(20) NOT NULL,
    creator_id UUID REFERENCES users(id) ON DELETE SET NULL,
    last_modified_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE IF EXISTS glossary_entries
    ADD COLUMN IF NOT EXISTS note TEXT;
ALTER TABLE IF EXISTS glossary_entries
    ADD COLUMN IF NOT EXISTS source_normalized TEXT;
ALTER TABLE IF EXISTS glossary_entries
    ADD COLUMN IF NOT EXISTS source_language VARCHAR(20);
ALTER TABLE IF EXISTS glossary_entries
    ADD COLUMN IF NOT EXISTS target_language VARCHAR(20);
ALTER TABLE IF EXISTS glossary_entries
    ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS glossary_entries
    ADD COLUMN IF NOT EXISTS last_modified_by_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS glossary_entries
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();
ALTER TABLE IF EXISTS glossary_entries
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();

CREATE INDEX IF NOT EXISTS ix_glossary_entries_creator_id
    ON glossary_entries (creator_id);
CREATE INDEX IF NOT EXISTS ix_glossary_entries_last_modified_by_id
    ON glossary_entries (last_modified_by_id);

UPDATE glossary_entries
SET last_modified_by_id = creator_id
WHERE last_modified_by_id IS NULL
  AND creator_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_glossary_entries_glossary_base_id
    ON glossary_entries (glossary_base_id);
CREATE INDEX IF NOT EXISTS ix_glossary_entries_base_source_text
    ON glossary_entries (glossary_base_id, source_text);
CREATE INDEX IF NOT EXISTS ix_glossary_entries_base_source_normalized
    ON glossary_entries (glossary_base_id, source_normalized);
CREATE INDEX IF NOT EXISTS ix_glossary_entries_language_pair
    ON glossary_entries (source_language, target_language);

DROP TRIGGER IF EXISTS update_glossary_entries_updated_at ON glossary_entries;
CREATE TRIGGER update_glossary_entries_updated_at
    BEFORE UPDATE ON glossary_entries
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS glossary_base_ids TEXT NOT NULL DEFAULT '[]';

CREATE TABLE IF NOT EXISTS document_statistics_reports (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    created_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    file_ids TEXT NOT NULL DEFAULT '[]',
    total_files INTEGER NOT NULL DEFAULT 0,
    available_files INTEGER NOT NULL DEFAULT 0,
    totals TEXT NOT NULL DEFAULT '{}',
    status VARCHAR(20) NOT NULL DEFAULT 'completed',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS document_statistics_report_items (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    report_id UUID NOT NULL REFERENCES document_statistics_reports(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_record_id UUID REFERENCES file_records(id) ON DELETE SET NULL,
    file_name VARCHAR(255) NOT NULL,
    source_language VARCHAR(20),
    target_language VARCHAR(20),
    file_size_bytes INTEGER,
    statistics TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_document_statistics_reports_project_id
    ON document_statistics_reports (project_id);
CREATE INDEX IF NOT EXISTS ix_document_statistics_reports_created_by_id
    ON document_statistics_reports (created_by_id);
CREATE INDEX IF NOT EXISTS ix_document_statistics_reports_created_at
    ON document_statistics_reports (created_at);
CREATE INDEX IF NOT EXISTS ix_document_statistics_report_items_report_id
    ON document_statistics_report_items (report_id);
CREATE INDEX IF NOT EXISTS ix_document_statistics_report_items_project_id
    ON document_statistics_report_items (project_id);
CREATE INDEX IF NOT EXISTS ix_document_statistics_report_items_file_record_id
    ON document_statistics_report_items (file_record_id);

COMMIT;
