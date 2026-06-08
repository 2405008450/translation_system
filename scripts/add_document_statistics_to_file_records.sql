-- 为 file_records 添加 DOCX 文档级统计缓存。
ALTER TABLE file_records
    ADD COLUMN IF NOT EXISTS document_statistics TEXT NOT NULL DEFAULT '{}';

-- 持久化每次点击“统计”生成的字数检验报告。
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
