ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS last_modified_by_id UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_segments_last_modified_by_id
    ON segments (last_modified_by_id);

CREATE TABLE IF NOT EXISTS pretranslation_runs (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    progress INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    total_files INTEGER NOT NULL DEFAULT 0,
    completed_files INTEGER NOT NULL DEFAULT 0,
    failed_files INTEGER NOT NULL DEFAULT 0,
    canceled_files INTEGER NOT NULL DEFAULT 0,
    options_json TEXT NOT NULL DEFAULT '{}',
    created_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pretranslation_tasks (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    run_id UUID NOT NULL REFERENCES pretranslation_runs(id) ON DELETE CASCADE,
    file_record_id UUID NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    stage VARCHAR(40) NOT NULL DEFAULT 'queued',
    progress INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    provider VARCHAR(40),
    model VARCHAR(200),
    scope VARCHAR(40),
    total_segments INTEGER NOT NULL DEFAULT 0,
    unique_segments INTEGER NOT NULL DEFAULT 0,
    deduplicated_segments INTEGER NOT NULL DEFAULT 0,
    processed_segments INTEGER NOT NULL DEFAULT 0,
    updated_segments INTEGER NOT NULL DEFAULT 0,
    error_segments INTEGER NOT NULL DEFAULT 0,
    current_action TEXT,
    operation_token VARCHAR(64),
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    error TEXT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    last_heartbeat_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_pretranslation_runs_project_id
    ON pretranslation_runs (project_id);

CREATE INDEX IF NOT EXISTS ix_pretranslation_runs_status
    ON pretranslation_runs (status);

CREATE INDEX IF NOT EXISTS ix_pretranslation_runs_created_by_id
    ON pretranslation_runs (created_by_id);

CREATE INDEX IF NOT EXISTS ix_pretranslation_runs_created_at
    ON pretranslation_runs (created_at);

CREATE INDEX IF NOT EXISTS ix_pretranslation_tasks_run_id
    ON pretranslation_tasks (run_id);

CREATE INDEX IF NOT EXISTS ix_pretranslation_tasks_file_record_id
    ON pretranslation_tasks (file_record_id);

CREATE INDEX IF NOT EXISTS ix_pretranslation_tasks_file_status
    ON pretranslation_tasks (file_record_id, status);

CREATE INDEX IF NOT EXISTS ix_pretranslation_tasks_status
    ON pretranslation_tasks (status);

CREATE INDEX IF NOT EXISTS ix_pretranslation_tasks_updated_at
    ON pretranslation_tasks (updated_at);
