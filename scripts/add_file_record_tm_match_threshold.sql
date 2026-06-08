-- 为项目文件增加 TM 匹配率阈值，项目设置中的翻译记忆库模块会读写该字段。
ALTER TABLE IF EXISTS file_records
    ADD COLUMN IF NOT EXISTS tm_match_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.8;

UPDATE file_records
SET tm_match_threshold = 0.8
WHERE tm_match_threshold IS NULL;
