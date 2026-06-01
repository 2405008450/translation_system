-- 添加 collection_ids_json 字段到 file_records 表
-- 用于存储多个记忆库 ID，支持预翻译时选择多个记忆库后在匹配面板中显示所有匹配结果

ALTER TABLE file_records
ADD COLUMN IF NOT EXISTS collection_ids_json TEXT NOT NULL DEFAULT '[]';

-- 将现有的 collection_id 迁移到 collection_ids_json
UPDATE file_records
SET collection_ids_json = CASE
    WHEN collection_id IS NOT NULL THEN '["' || collection_id::text || '"]'
    ELSE '[]'
END
WHERE collection_ids_json = '[]';
