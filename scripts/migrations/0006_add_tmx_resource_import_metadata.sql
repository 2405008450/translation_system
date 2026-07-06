-- 为大文件 TMX 资源导入补充批次和条目元数据字段。

CREATE TABLE IF NOT EXISTS resource_import_batches (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    resource_type VARCHAR(20) NOT NULL,
    resource_id UUID,
    filename TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL DEFAULT 0,
    file_format VARCHAR(20) NOT NULL DEFAULT '',
    source_language VARCHAR(20),
    target_language VARCHAR(20),
    tmx_header_metadata JSONB,
    created_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_resource_import_batches_resource
    ON resource_import_batches (resource_type, resource_id);
CREATE INDEX IF NOT EXISTS ix_resource_import_batches_created_at
    ON resource_import_batches (created_at);

ALTER TABLE IF EXISTS memory_entries
    ADD COLUMN IF NOT EXISTS external_tuid TEXT;
ALTER TABLE IF EXISTS memory_entries
    ADD COLUMN IF NOT EXISTS tmx_metadata JSONB;
ALTER TABLE IF EXISTS memory_entries
    ADD COLUMN IF NOT EXISTS import_batch_id UUID REFERENCES resource_import_batches(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_memory_entries_external_tuid
    ON memory_entries (external_tuid);
CREATE INDEX IF NOT EXISTS ix_memory_entries_import_batch_id
    ON memory_entries (import_batch_id);

ALTER TABLE IF EXISTS term_entries
    ADD COLUMN IF NOT EXISTS external_tuid TEXT;
ALTER TABLE IF EXISTS term_entries
    ADD COLUMN IF NOT EXISTS tmx_metadata JSONB;
ALTER TABLE IF EXISTS term_entries
    ADD COLUMN IF NOT EXISTS import_batch_id UUID REFERENCES resource_import_batches(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_term_entries_external_tuid
    ON term_entries (external_tuid);
CREATE INDEX IF NOT EXISTS ix_term_entries_import_batch_id
    ON term_entries (import_batch_id);
