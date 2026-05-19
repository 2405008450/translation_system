-- 为 file_records 表添加项目管理所需的字段
-- 运行方式: psql -U <user> -d <database> -f scripts/add_project_fields.sql

BEGIN;

ALTER TABLE file_records ADD COLUMN IF NOT EXISTS source_language VARCHAR(20);
ALTER TABLE file_records ADD COLUMN IF NOT EXISTS target_language VARCHAR(20);
ALTER TABLE file_records ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE file_records ADD COLUMN IF NOT EXISTS deadline TIMESTAMP;
ALTER TABLE file_records ADD COLUMN IF NOT EXISTS access_level VARCHAR(20) NOT NULL DEFAULT 'team';

CREATE INDEX IF NOT EXISTS ix_file_records_creator_id ON file_records(creator_id);
CREATE INDEX IF NOT EXISTS ix_file_records_source_language ON file_records(source_language);
CREATE INDEX IF NOT EXISTS ix_file_records_status ON file_records(status);

COMMIT;
