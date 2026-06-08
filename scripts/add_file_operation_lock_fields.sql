-- 为预翻译等后台写入增加文件级操作锁，避免与人工编辑并发写同一批句段。
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS active_operation VARCHAR(40);

ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS active_operation_token VARCHAR(64);

ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS active_operation_updated_at TIMESTAMP;

ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS active_operation_user_id UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_file_records_active_operation
    ON file_records (active_operation);
