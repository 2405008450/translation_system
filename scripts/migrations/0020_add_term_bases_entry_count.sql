-- 持久化 term_bases.entry_count，避免术语库列表每次聚合全部 term_entries。
-- 触发器与初始回填在同一事务中完成，保证写入锁定期间计数快照一致。

BEGIN;

ALTER TABLE term_bases
    ADD COLUMN IF NOT EXISTS entry_count BIGINT NOT NULL DEFAULT 0;

CREATE OR REPLACE FUNCTION term_bases_entry_count_on_insert()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE term_bases tb
    SET entry_count = tb.entry_count + d.cnt,
        updated_at = NOW()
    FROM (
        SELECT term_base_id, count(*) AS cnt
        FROM new_entries
        WHERE term_base_id IS NOT NULL
        GROUP BY term_base_id
    ) d
    WHERE tb.id = d.term_base_id;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION term_bases_entry_count_on_delete()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE term_bases tb
    SET entry_count = GREATEST(tb.entry_count - d.cnt, 0),
        updated_at = NOW()
    FROM (
        SELECT term_base_id, count(*) AS cnt
        FROM old_entries
        WHERE term_base_id IS NOT NULL
        GROUP BY term_base_id
    ) d
    WHERE tb.id = d.term_base_id;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION term_bases_entry_count_on_update()
RETURNS TRIGGER AS $$
BEGIN
    -- 同库更新只推进水位；跨库移动同时调整两边计数。
    UPDATE term_bases tb
    SET entry_count = GREATEST(tb.entry_count + d.delta, 0),
        updated_at = NOW()
    FROM (
        SELECT term_base_id, SUM(delta) AS delta
        FROM (
            SELECT term_base_id, count(*) AS delta
            FROM new_entries
            WHERE term_base_id IS NOT NULL
            GROUP BY term_base_id
            UNION ALL
            SELECT term_base_id, -count(*) AS delta
            FROM old_entries
            WHERE term_base_id IS NOT NULL
            GROUP BY term_base_id
        ) changes
        GROUP BY term_base_id
    ) d
    WHERE tb.id = d.term_base_id;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    needs_backfill BOOLEAN := FALSE;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'term_entries_entry_count_ins_trg'
    ) THEN
        CREATE TRIGGER term_entries_entry_count_ins_trg
            AFTER INSERT ON term_entries
            REFERENCING NEW TABLE AS new_entries
            FOR EACH STATEMENT
            EXECUTE FUNCTION term_bases_entry_count_on_insert();
        needs_backfill := TRUE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'term_entries_entry_count_del_trg'
    ) THEN
        CREATE TRIGGER term_entries_entry_count_del_trg
            AFTER DELETE ON term_entries
            REFERENCING OLD TABLE AS old_entries
            FOR EACH STATEMENT
            EXECUTE FUNCTION term_bases_entry_count_on_delete();
        needs_backfill := TRUE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'term_entries_entry_count_upd_trg'
    ) THEN
        CREATE TRIGGER term_entries_entry_count_upd_trg
            AFTER UPDATE ON term_entries
            REFERENCING OLD TABLE AS old_entries NEW TABLE AS new_entries
            FOR EACH STATEMENT
            EXECUTE FUNCTION term_bases_entry_count_on_update();
        needs_backfill := TRUE;
    END IF;

    IF needs_backfill THEN
        UPDATE term_bases SET entry_count = 0;
        UPDATE term_bases tb
        SET entry_count = counts.cnt
        FROM (
            SELECT term_base_id, count(*) AS cnt
            FROM term_entries
            WHERE term_base_id IS NOT NULL
            GROUP BY term_base_id
        ) counts
        WHERE tb.id = counts.term_base_id;
    END IF;
END $$;

COMMIT;
