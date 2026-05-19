-- 为 projects 表增加翻译细则字段
-- 用于存储项目级别的翻译规范/要求文本，在 LLM 翻译时注入到 prompt 中

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS translation_guidelines TEXT NOT NULL DEFAULT '';
