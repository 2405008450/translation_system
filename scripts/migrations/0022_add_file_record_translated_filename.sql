-- 文件名翻译：为 file_records 增加可选的“译名”字段，导出时可选用它作为输出文件名。

ALTER TABLE file_records
    ADD COLUMN IF NOT EXISTS translated_filename VARCHAR(255);
