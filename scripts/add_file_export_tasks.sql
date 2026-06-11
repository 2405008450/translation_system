CREATE TABLE IF NOT EXISTS file_export_tasks (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid
);

ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS file_record_id UUID REFERENCES file_records(id) ON DELETE CASCADE;
ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS export_type VARCHAR(40) NOT NULL DEFAULT 'original';
ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'queued';
ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS progress INTEGER NOT NULL DEFAULT 0;
ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS message TEXT NOT NULL DEFAULT '';
ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS result_path TEXT;
ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS filename VARCHAR(255);
ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS media_type VARCHAR(120);
ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS size_bytes INTEGER;
ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS error TEXT;
ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS created_by_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW();
ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT NOW();
ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP NOT NULL DEFAULT (NOW() + INTERVAL '24 hours');

CREATE INDEX IF NOT EXISTS ix_file_export_tasks_file_record_type
    ON file_export_tasks (file_record_id, export_type);

CREATE INDEX IF NOT EXISTS ix_file_export_tasks_status
    ON file_export_tasks (status);

CREATE INDEX IF NOT EXISTS ix_file_export_tasks_expires_at
    ON file_export_tasks (expires_at);
