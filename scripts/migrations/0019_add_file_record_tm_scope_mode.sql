-- 记忆库检索范围：selected 使用文件绑定的库；language_pair_all 仅按语言对检索。
-- 后者避免“全选数百个库”时生成庞大的 collection_id IN (...) 条件。

ALTER TABLE file_records
    ADD COLUMN IF NOT EXISTS tm_scope_mode VARCHAR(24) NOT NULL DEFAULT 'selected';

UPDATE file_records
SET tm_scope_mode = 'selected'
WHERE tm_scope_mode IS NULL
   OR tm_scope_mode NOT IN ('selected', 'language_pair_all');
