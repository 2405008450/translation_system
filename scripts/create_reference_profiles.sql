-- 参考分析相关表
-- 存储任务级别的参考文件分析结果

-- 参考分析 Profile 表
CREATE TABLE IF NOT EXISTS reference_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_record_id UUID REFERENCES file_records(id) ON DELETE CASCADE,
    source_files TEXT NOT NULL DEFAULT '[]',
    terminology TEXT NOT NULL DEFAULT '[]',
    translation_memory TEXT NOT NULL DEFAULT '[]',
    style_guide TEXT,
    analysis_report TEXT,
    match_result TEXT,
    overall_confidence FLOAT DEFAULT 0.0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE IF EXISTS reference_profiles
    ADD COLUMN IF NOT EXISTS match_result TEXT;

-- 索引
CREATE INDEX IF NOT EXISTS ix_reference_profiles_file_record_id 
    ON reference_profiles(file_record_id);

-- 参考文件记录表（存储上传的参考文件信息）
CREATE TABLE IF NOT EXISTS reference_files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    profile_id UUID REFERENCES reference_profiles(id) ON DELETE CASCADE,
    filename VARCHAR(500) NOT NULL,
    file_path VARCHAR(1000) NOT NULL,
    file_type VARCHAR(50),  -- terminology, tm, style_guide, bilingual, mixed, unknown
    file_size INTEGER,
    is_bilingual_source BOOLEAN DEFAULT FALSE,
    is_bilingual_target BOOLEAN DEFAULT FALSE,
    bilingual_pair_id UUID,  -- 关联的双语对文件ID
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_reference_files_profile_id 
    ON reference_files(profile_id);

-- 更新时间触发器
CREATE OR REPLACE FUNCTION update_reference_profiles_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_reference_profiles_updated_at ON reference_profiles;
CREATE TRIGGER trigger_reference_profiles_updated_at
    BEFORE UPDATE ON reference_profiles
    FOR EACH ROW
    EXECUTE FUNCTION update_reference_profiles_updated_at();

-- 添加注释
COMMENT ON TABLE reference_profiles IS '参考文件分析结果，存储术语、TM、风格和分析报告';
COMMENT ON TABLE reference_files IS '参考文件记录，存储上传的参考文件元信息';
COMMENT ON COLUMN reference_profiles.terminology IS '提取的术语列表 JSON';
COMMENT ON COLUMN reference_profiles.translation_memory IS '提取的翻译记忆句对 JSON';
COMMENT ON COLUMN reference_profiles.analysis_report IS 'AI 深度分析报告 JSON';
COMMENT ON COLUMN reference_profiles.match_result IS '匹配结果JSON，包含exact_matches、fuzzy_matches、term_matches';
