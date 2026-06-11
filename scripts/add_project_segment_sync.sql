-- 同项目重复句段同步：为项目内精确原文匹配增加句段哈希字段。

ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS source_hash VARCHAR(64);

ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS project_sync_disabled BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS ix_segments_source_hash
    ON segments (source_hash);

CREATE INDEX IF NOT EXISTS ix_segments_file_source_hash
    ON segments (file_record_id, source_hash);
