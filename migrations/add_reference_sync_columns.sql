-- 为参考文件提取的术语和翻译记忆同步到项目级 词汇表/记忆库 提供支持
-- 在 Navicat 里连接到本项目数据库，新建查询，整段粘贴执行即可。
-- 用有 DDL 权限的账号执行（postgres 超管或库 owner）。

BEGIN;

-- 1) memory_bases 加项目归属与来源标记
ALTER TABLE IF EXISTS memory_bases
    ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE CASCADE;
ALTER TABLE IF EXISTS memory_bases
    ADD COLUMN IF NOT EXISTS origin VARCHAR(20) NOT NULL DEFAULT 'manual';
CREATE INDEX IF NOT EXISTS ix_memory_bases_project_id ON memory_bases (project_id);
CREATE INDEX IF NOT EXISTS ix_memory_bases_origin ON memory_bases (origin);

-- 2) glossary_bases 加项目归属与来源标记
ALTER TABLE IF EXISTS glossary_bases
    ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE CASCADE;
ALTER TABLE IF EXISTS glossary_bases
    ADD COLUMN IF NOT EXISTS origin VARCHAR(20) NOT NULL DEFAULT 'manual';
CREATE INDEX IF NOT EXISTS ix_glossary_bases_project_id ON glossary_bases (project_id);
CREATE INDEX IF NOT EXISTS ix_glossary_bases_origin ON glossary_bases (origin);

-- 3) reference_profiles 加项目归属和指向同步出来的两个 base
ALTER TABLE IF EXISTS reference_profiles
    ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE CASCADE;
ALTER TABLE IF EXISTS reference_profiles
    ADD COLUMN IF NOT EXISTS glossary_base_id UUID REFERENCES glossary_bases(id) ON DELETE SET NULL;
ALTER TABLE IF EXISTS reference_profiles
    ADD COLUMN IF NOT EXISTS memory_base_id UUID REFERENCES memory_bases(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS ix_reference_profiles_project_id ON reference_profiles (project_id);

COMMIT;
