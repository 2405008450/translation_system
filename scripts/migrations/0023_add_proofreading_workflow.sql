-- 多语种 Excel 校对工作流、批次、列映射和不可变原译文基线。

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS workflow_template_id VARCHAR(40) NOT NULL DEFAULT 'custom';

CREATE TABLE IF NOT EXISTS proofreading_batches (
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
    filename VARCHAR(255) NOT NULL,
    file_hash VARCHAR(64) NOT NULL DEFAULT '',
    source_language VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ready',
    progress INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    total_segments INTEGER NOT NULL DEFAULT 0,
    changed_segments INTEGER NOT NULL DEFAULT 0,
    skipped_segments INTEGER NOT NULL DEFAULT 0,
    failed_segments INTEGER NOT NULL DEFAULT 0,
    export_status VARCHAR(20) NOT NULL DEFAULT 'idle',
    export_progress INTEGER NOT NULL DEFAULT 0,
    export_error_message TEXT NOT NULL DEFAULT '',
    export_filename VARCHAR(255) NOT NULL DEFAULT '',
    export_path TEXT NOT NULL DEFAULT '',
    config_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP NULL
);

CREATE TABLE IF NOT EXISTS proofreading_column_bindings (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    batch_id UUID NOT NULL REFERENCES proofreading_batches(id) ON DELETE CASCADE,
    file_record_id UUID NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
    sheet_index INTEGER NOT NULL,
    sheet_name VARCHAR(255) NOT NULL,
    header_row INTEGER NOT NULL,
    source_column INTEGER NOT NULL,
    target_column INTEGER NOT NULL,
    output_column INTEGER NOT NULL,
    source_header TEXT NOT NULL DEFAULT '',
    target_header TEXT NOT NULL DEFAULT '',
    target_language VARCHAR(20) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_proofreading_binding_target UNIQUE (batch_id, sheet_index, target_column)
);

CREATE TABLE IF NOT EXISTS proofreading_segment_baselines (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    batch_id UUID NOT NULL REFERENCES proofreading_batches(id) ON DELETE CASCADE,
    binding_id UUID NOT NULL REFERENCES proofreading_column_bindings(id) ON DELETE CASCADE,
    segment_id UUID NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
    sheet_index INTEGER NOT NULL,
    row_index INTEGER NOT NULL,
    source_cell_ref VARCHAR(20) NOT NULL,
    target_cell_ref VARCHAR(20) NOT NULL,
    original_target_text TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_proofreading_segment_baseline_segment UNIQUE (segment_id)
);

ALTER TABLE translation_review_reports
    ADD COLUMN IF NOT EXISTS report_mode VARCHAR(30) NOT NULL DEFAULT 'issue_check';

ALTER TABLE translation_review_reports
    ADD COLUMN IF NOT EXISTS proofreading_batch_id UUID NULL
        REFERENCES proofreading_batches(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS ix_proofreading_batches_project_id
    ON proofreading_batches(project_id);
CREATE INDEX IF NOT EXISTS ix_proofreading_batches_status
    ON proofreading_batches(status);
CREATE INDEX IF NOT EXISTS ix_proofreading_batches_created_by_id
    ON proofreading_batches(created_by_id);
CREATE INDEX IF NOT EXISTS ix_proofreading_batches_created_at
    ON proofreading_batches(created_at);
CREATE INDEX IF NOT EXISTS ix_proofreading_column_bindings_batch_id
    ON proofreading_column_bindings(batch_id);
CREATE INDEX IF NOT EXISTS ix_proofreading_column_bindings_file_record_id
    ON proofreading_column_bindings(file_record_id);
CREATE INDEX IF NOT EXISTS ix_proofreading_segment_baselines_batch_id
    ON proofreading_segment_baselines(batch_id);
CREATE INDEX IF NOT EXISTS ix_proofreading_segment_baselines_binding_id
    ON proofreading_segment_baselines(binding_id);
CREATE INDEX IF NOT EXISTS ix_proofreading_segment_baselines_segment_id
    ON proofreading_segment_baselines(segment_id);
CREATE INDEX IF NOT EXISTS ix_translation_review_reports_proofreading_batch_id
    ON translation_review_reports(proofreading_batch_id);
