-- 项目重复句段同步 outbox：以 (project_id, 语言对, source_hash) 为唯一键，
-- 连续确认同一句段只保留一条待处理任务，由 segment-sync worker 批量消费。

BEGIN;

CREATE TABLE IF NOT EXISTS project_segment_sync_outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_language VARCHAR(20) NOT NULL DEFAULT '',
    target_language VARCHAR(20) NOT NULL DEFAULT '',
    source_hash VARCHAR(64) NOT NULL,
    source_file_record_id UUID NULL REFERENCES file_records(id) ON DELETE SET NULL,
    source_segment_id UUID NULL REFERENCES segments(id) ON DELETE SET NULL,
    requested_by_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT NOT NULL DEFAULT '',
    last_enqueued_at TIMESTAMP NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMP NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_project_sync_outbox_scope
    ON project_segment_sync_outbox (project_id, source_language, target_language, source_hash);
CREATE INDEX IF NOT EXISTS ix_project_sync_outbox_status_enqueued
    ON project_segment_sync_outbox (status, last_enqueued_at);

COMMIT;
