-- =============================================================================
-- 新增预翻译词汇表：glossary_bases / glossary_entries / file_records.glossary_base_ids
-- 运行方式: psql -U <user> -d <database> -f scripts/add_glossary_tables.sql
-- 该脚本是幂等的，可在已有库上重复执行。
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS glossary_bases (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    name VARCHAR(120) NOT NULL,
    description TEXT,
    source_language VARCHAR(20) NOT NULL,
    target_language VARCHAR(20) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_glossary_bases_name
    ON glossary_bases (name);
CREATE INDEX IF NOT EXISTS ix_glossary_bases_language_pair
    ON glossary_bases (source_language, target_language);

DROP TRIGGER IF EXISTS update_glossary_bases_updated_at ON glossary_bases;
CREATE TRIGGER update_glossary_bases_updated_at
    BEFORE UPDATE ON glossary_bases
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TABLE IF NOT EXISTS glossary_entries (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    glossary_base_id UUID NOT NULL REFERENCES glossary_bases(id) ON DELETE CASCADE,
    source_text TEXT NOT NULL,
    target_text TEXT NOT NULL,
    note TEXT,
    source_normalized TEXT,
    source_language VARCHAR(20) NOT NULL,
    target_language VARCHAR(20) NOT NULL,
    creator_id UUID REFERENCES users(id) ON DELETE SET NULL,
    last_modified_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE IF EXISTS glossary_entries
    ADD COLUMN IF NOT EXISTS note TEXT;
ALTER TABLE IF EXISTS glossary_entries
    ADD COLUMN IF NOT EXISTS source_normalized TEXT;
ALTER TABLE IF EXISTS glossary_entries
    ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS glossary_entries
    ADD COLUMN IF NOT EXISTS last_modified_by_id UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_glossary_entries_creator_id
    ON glossary_entries (creator_id);
CREATE INDEX IF NOT EXISTS ix_glossary_entries_last_modified_by_id
    ON glossary_entries (last_modified_by_id);

UPDATE glossary_entries
SET last_modified_by_id = creator_id
WHERE last_modified_by_id IS NULL
  AND creator_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_glossary_entries_glossary_base_id
    ON glossary_entries (glossary_base_id);
CREATE INDEX IF NOT EXISTS ix_glossary_entries_base_source_text
    ON glossary_entries (glossary_base_id, source_text);
CREATE INDEX IF NOT EXISTS ix_glossary_entries_base_source_normalized
    ON glossary_entries (glossary_base_id, source_normalized);
CREATE INDEX IF NOT EXISTS ix_glossary_entries_language_pair
    ON glossary_entries (source_language, target_language);

DROP TRIGGER IF EXISTS update_glossary_entries_updated_at ON glossary_entries;
CREATE TRIGGER update_glossary_entries_updated_at
    BEFORE UPDATE ON glossary_entries
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS glossary_base_ids TEXT NOT NULL DEFAULT '[]';

COMMIT;
