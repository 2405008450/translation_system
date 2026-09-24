-- 大型 AI 的源文件或导出结果可能接近/超过 PostgreSQL INTEGER 上限。
-- USING 转换对 INTEGER -> BIGINT 无损，重复执行时仍保持 BIGINT。
ALTER TABLE IF EXISTS file_export_tasks
    ADD COLUMN IF NOT EXISTS size_bytes BIGINT;
ALTER TABLE IF EXISTS file_export_tasks
    ALTER COLUMN size_bytes TYPE BIGINT USING size_bytes::BIGINT;
