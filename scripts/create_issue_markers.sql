-- =============================================================================
-- 灰度问题标记表
-- -----------------------------------------------------------------------------
-- 用户可在项目或具体文件任务上标记问题，便于灰度测试后追踪处理。
-- =============================================================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS issue_markers (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_record_id UUID REFERENCES file_records(id) ON DELETE SET NULL,
    title VARCHAR(160) NOT NULL DEFAULT '',
    description TEXT NOT NULL,
    category VARCHAR(30) NOT NULL DEFAULT 'other',
    severity VARCHAR(20) NOT NULL DEFAULT 'medium',
    status VARCHAR(20) NOT NULL DEFAULT 'open',
    page_url TEXT,
    user_agent TEXT,
    reporter_id UUID REFERENCES users(id) ON DELETE SET NULL,
    resolved_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_issue_markers_project_id
    ON issue_markers (project_id);
CREATE INDEX IF NOT EXISTS ix_issue_markers_file_record_id
    ON issue_markers (file_record_id);
CREATE INDEX IF NOT EXISTS ix_issue_markers_status
    ON issue_markers (status);
CREATE INDEX IF NOT EXISTS ix_issue_markers_reporter_id
    ON issue_markers (reporter_id);

DROP TRIGGER IF EXISTS update_issue_markers_updated_at ON issue_markers;
CREATE TRIGGER update_issue_markers_updated_at
    BEFORE UPDATE ON issue_markers
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
