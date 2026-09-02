-- 失焦句段同步：记录触发来源与源句段版本，并用 generation 拒绝旧任务提交。
-- 大表索引使用 CONCURRENTLY；本脚本不能放入显式事务。

ALTER TABLE IF EXISTS project_segment_sync_outbox
    ADD COLUMN IF NOT EXISTS source_version INTEGER NULL;
ALTER TABLE IF EXISTS project_segment_sync_outbox
    ADD COLUMN IF NOT EXISTS trigger_kind VARCHAR(20) NOT NULL DEFAULT 'confirmed';
ALTER TABLE IF EXISTS project_segment_sync_outbox
    ADD COLUMN IF NOT EXISTS generation INTEGER NOT NULL DEFAULT 1;

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_project_sync_outbox_pending_enqueued
    ON project_segment_sync_outbox (last_enqueued_at, id)
    WHERE status = 'pending';
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_project_sync_outbox_processing_updated
    ON project_segment_sync_outbox (updated_at, id)
    WHERE status = 'processing';

DROP INDEX CONCURRENTLY IF EXISTS ix_project_sync_outbox_status_enqueued;
