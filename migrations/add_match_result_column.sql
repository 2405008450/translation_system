-- 添加匹配结果持久化字段到 reference_profiles 表
-- 用于保存执行匹配后的结果，刷新页面后可以恢复显示

ALTER TABLE reference_profiles 
ADD COLUMN IF NOT EXISTS match_result TEXT;

COMMENT ON COLUMN reference_profiles.match_result IS '匹配结果JSON，包含exact_matches、fuzzy_matches、term_matches';
