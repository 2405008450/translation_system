-- memory_entries 的原文和归一化原文均为 TEXT。PostgreSQL B-tree 的单个
-- 索引项不能超过约 1/3 个缓冲页；长 TM 句段会因此触发 ProgramLimitExceeded，
-- 令整批导入回滚。精确匹配已有 source_hash，模糊检索已有 trigram GIN，
-- 所以下列 TEXT 全值 B-tree 索引既不安全也没有必要。
--
-- 生产库数据量较大，使用 CONCURRENTLY 避免清理索引时长时间阻塞读写。
-- run_migrations.sh 通过 psql 直接执行单个文件，不会包裹显式事务。

DROP INDEX CONCURRENTLY IF EXISTS ix_memory_entries_collection_source_normalized;
DROP INDEX CONCURRENTLY IF EXISTS ix_memory_entries_source_text;
DROP INDEX CONCURRENTLY IF EXISTS ix_memory_entries_source_normalized;
