-- 为参考文件提取结果同步到项目级术语库、翻译记忆库提供关联字段。
BEGIN;

ALTER TABLE IF EXISTS memory_bases
    ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE CASCADE;
ALTER TABLE IF EXISTS memory_bases
    ADD COLUMN IF NOT EXISTS origin VARCHAR(20) NOT NULL DEFAULT 'manual';
CREATE INDEX IF NOT EXISTS ix_memory_bases_project_id ON memory_bases (project_id);
CREATE INDEX IF NOT EXISTS ix_memory_bases_origin ON memory_bases (origin);

ALTER TABLE IF EXISTS glossary_bases
    ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE CASCADE;
ALTER TABLE IF EXISTS glossary_bases
    ADD COLUMN IF NOT EXISTS origin VARCHAR(20) NOT NULL DEFAULT 'manual';
CREATE INDEX IF NOT EXISTS ix_glossary_bases_project_id ON glossary_bases (project_id);
CREATE INDEX IF NOT EXISTS ix_glossary_bases_origin ON glossary_bases (origin);

ALTER TABLE IF EXISTS reference_profiles
    ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE CASCADE;
ALTER TABLE IF EXISTS reference_profiles
    ADD COLUMN IF NOT EXISTS glossary_base_id UUID REFERENCES glossary_bases(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS reference_profiles
    ADD COLUMN IF NOT EXISTS memory_base_id UUID REFERENCES memory_bases(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS ix_reference_profiles_project_id ON reference_profiles (project_id);

COMMIT;
