-- 持久化 memory_bases.entry_count，由语句级触发器维护，
-- 替代资源列表接口对 1300 万级 memory_entries 的实时 COUNT + GROUP BY。
-- 触发器创建与初始回填在同一事务内完成：CREATE TRIGGER 会短暂阻塞
-- memory_entries 写入（数秒），保证回填快照与触发器增量无缝衔接。

BEGIN;

ALTER TABLE memory_bases
    ADD COLUMN IF NOT EXISTS entry_count BIGINT NOT NULL DEFAULT 0;

CREATE OR REPLACE FUNCTION memory_bases_entry_count_on_insert()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE memory_bases mb
    SET entry_count = mb.entry_count + d.cnt,
        updated_at = NOW()
    FROM (
        SELECT collection_id, count(*) AS cnt
        FROM new_entries
        WHERE collection_id IS NOT NULL
        GROUP BY collection_id
    ) d
    WHERE mb.id = d.collection_id;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION memory_bases_entry_count_on_delete()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE memory_bases mb
    SET entry_count = GREATEST(mb.entry_count - d.cnt, 0),
        updated_at = NOW()
    FROM (
        SELECT collection_id, count(*) AS cnt
        FROM old_entries
        WHERE collection_id IS NOT NULL
        GROUP BY collection_id
    ) d
    WHERE mb.id = d.collection_id;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION memory_bases_entry_count_on_update()
RETURNS TRIGGER AS $$
BEGIN
    -- 同库更新只推进 updated_at；跨库移动同时调整两边计数。
    -- 不过滤 delta=0，确保译文、原文或元数据变化会使 TM 签名失效。
    UPDATE memory_bases mb
    SET entry_count = GREATEST(mb.entry_count + d.delta, 0),
        updated_at = NOW()
    FROM (
        SELECT collection_id, SUM(delta) AS delta
        FROM (
            SELECT collection_id, count(*) AS delta
            FROM new_entries
            WHERE collection_id IS NOT NULL
            GROUP BY collection_id
            UNION ALL
            SELECT collection_id, -count(*) AS delta
            FROM old_entries
            WHERE collection_id IS NOT NULL
            GROUP BY collection_id
        ) x
        GROUP BY collection_id
    ) d
    WHERE mb.id = d.collection_id;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    needs_backfill BOOLEAN := FALSE;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'memory_entries_entry_count_ins_trg'
    ) THEN
        CREATE TRIGGER memory_entries_entry_count_ins_trg
            AFTER INSERT ON memory_entries
            REFERENCING NEW TABLE AS new_entries
            FOR EACH STATEMENT
            EXECUTE FUNCTION memory_bases_entry_count_on_insert();
        needs_backfill := TRUE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'memory_entries_entry_count_del_trg'
    ) THEN
        CREATE TRIGGER memory_entries_entry_count_del_trg
            AFTER DELETE ON memory_entries
            REFERENCING OLD TABLE AS old_entries
            FOR EACH STATEMENT
            EXECUTE FUNCTION memory_bases_entry_count_on_delete();
        needs_backfill := TRUE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'memory_entries_entry_count_upd_trg'
    ) THEN
        CREATE TRIGGER memory_entries_entry_count_upd_trg
            AFTER UPDATE ON memory_entries
            REFERENCING OLD TABLE AS old_entries NEW TABLE AS new_entries
            FOR EACH STATEMENT
            EXECUTE FUNCTION memory_bases_entry_count_on_update();
        needs_backfill := TRUE;
    END IF;

    IF needs_backfill THEN
        -- 初始回填：先清零，再用一次 GROUP BY 扫描写回非空库。
        -- 与触发器创建同事务，写入被锁定期间快照一致。
        UPDATE memory_bases SET entry_count = 0;
        UPDATE memory_bases mb
        SET entry_count = counts.cnt
        FROM (
            SELECT collection_id, count(*) AS cnt
            FROM memory_entries
            WHERE collection_id IS NOT NULL
            GROUP BY collection_id
        ) counts
        WHERE mb.id = counts.collection_id;
    END IF;
END $$;

COMMIT;
