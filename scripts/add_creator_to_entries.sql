-- 为 TM、术语和 Glossary 条目补充创建人、最后修改人字段。

ALTER TABLE IF EXISTS memory_entries
  ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS memory_entries
  ADD COLUMN IF NOT EXISTS last_modified_by_id UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_memory_entries_creator_id
  ON memory_entries (creator_id);
CREATE INDEX IF NOT EXISTS ix_memory_entries_last_modified_by_id
  ON memory_entries (last_modified_by_id);

UPDATE memory_entries
SET last_modified_by_id = creator_id
WHERE last_modified_by_id IS NULL
  AND creator_id IS NOT NULL;

ALTER TABLE IF EXISTS term_entries
  ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS term_entries
  ADD COLUMN IF NOT EXISTS last_modified_by_id UUID REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_term_entries_creator_id
  ON term_entries (creator_id);
CREATE INDEX IF NOT EXISTS ix_term_entries_last_modified_by_id
  ON term_entries (last_modified_by_id);

UPDATE term_entries
SET last_modified_by_id = creator_id
WHERE last_modified_by_id IS NULL
  AND creator_id IS NOT NULL;

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
