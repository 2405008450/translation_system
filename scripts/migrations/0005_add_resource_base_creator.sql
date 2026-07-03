ALTER TABLE IF EXISTS memory_bases
    ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_memory_bases_creator_id
    ON memory_bases (creator_id);

ALTER TABLE IF EXISTS term_bases
    ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_term_bases_creator_id
    ON term_bases (creator_id);

ALTER TABLE IF EXISTS glossary_bases
    ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_glossary_bases_creator_id
    ON glossary_bases (creator_id);
