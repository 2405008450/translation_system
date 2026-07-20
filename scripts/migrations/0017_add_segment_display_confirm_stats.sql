-- 1) segments 新增持久化 display_index（文档内显示序号，0 起）与 confirmed_at（确认时间）。
-- 2) 新增 file_segment_stats 文件级句段统计表，由语句级触发器增量维护，
--    替代确认/轮询链路中对整个文件的实时聚合与 row_number() 计算。
-- 触发器创建与统计回填在同一事务内完成（短暂阻塞 segments 写入）。

BEGIN;

ALTER TABLE segments
    ADD COLUMN IF NOT EXISTS display_index INTEGER NOT NULL DEFAULT -1;
ALTER TABLE segments
    ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMP NULL;

CREATE INDEX IF NOT EXISTS ix_segments_file_display_index
    ON segments (file_record_id, display_index);
CREATE INDEX IF NOT EXISTS ix_segments_file_updated_at_id
    ON segments (file_record_id, updated_at, id);

-- 已确认句段的 confirmed_at 用 updated_at 兜底回填（历史数据无确认时间）。
UPDATE segments
SET confirmed_at = updated_at
WHERE status = 'confirmed' AND confirmed_at IS NULL;

-- display_index 回填：按现有 API 排序规则（app/services/file_record_service.py
-- 中 SEGMENT_ORDERING / PPTX_SEGMENT_ORDERING）计算。仅在存在未编号句段时执行。
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM segments WHERE display_index < 0 LIMIT 1) THEN
        -- 非 PPTX：block/row/cell 优先
        UPDATE segments s
        SET display_index = t.rn - 1
        FROM (
            SELECT s2.id,
                   row_number() OVER (
                       PARTITION BY s2.file_record_id
                       ORDER BY s2.block_index ASC,
                                s2.row_index ASC NULLS FIRST,
                                s2.cell_index ASC NULLS FIRST,
                                CASE WHEN s2.sequence_index >= 0 THEN 0 ELSE 1 END ASC,
                                s2.sequence_index ASC,
                                s2.sentence_id ASC
                   ) AS rn
            FROM segments s2
            JOIN file_records fr ON fr.id = s2.file_record_id
            WHERE lower(fr.filename) NOT LIKE '%.pptx'
        ) t
        WHERE s.id = t.id AND s.display_index <> t.rn - 1;

        -- PPTX：sequence 优先
        UPDATE segments s
        SET display_index = t.rn - 1
        FROM (
            SELECT s2.id,
                   row_number() OVER (
                       PARTITION BY s2.file_record_id
                       ORDER BY CASE WHEN s2.sequence_index >= 0 THEN 0 ELSE 1 END ASC,
                                s2.sequence_index ASC,
                                s2.sentence_id ASC,
                                s2.block_index ASC,
                                s2.row_index ASC NULLS FIRST,
                                s2.cell_index ASC NULLS FIRST
                   ) AS rn
            FROM segments s2
            JOIN file_records fr ON fr.id = s2.file_record_id
            WHERE lower(fr.filename) LIKE '%.pptx'
        ) t
        WHERE s.id = t.id AND s.display_index <> t.rn - 1;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- 文件句段统计表
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS file_segment_stats (
    file_record_id UUID PRIMARY KEY REFERENCES file_records(id) ON DELETE CASCADE,
    total INTEGER NOT NULL DEFAULT 0,
    exact_count INTEGER NOT NULL DEFAULT 0,
    fuzzy_count INTEGER NOT NULL DEFAULT 0,
    none_count INTEGER NOT NULL DEFAULT 0,
    confirmed_count INTEGER NOT NULL DEFAULT 0,
    empty_target_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- 与 app/services/segment_status.py 的 sql_normalize_match_text 保持一致。
CREATE OR REPLACE FUNCTION seg_norm_match_text(t TEXT)
RETURNS TEXT AS $$
    SELECT btrim(
        regexp_replace(
            btrim(
                regexp_replace(
                    regexp_replace(coalesce(t, ''), '[[:space:]]+', ' ', 'g'),
                    '[[:space:]]+([。！？!?.，,、；;：:）)\]}])',
                    '\1',
                    'g'
                )
            ),
            '[。！？!?.]+$',
            '',
            'g'
        )
    )
$$ LANGUAGE sql IMMUTABLE;

-- 与 sql_is_short_structural_fragment 保持一致。
CREATE OR REPLACE FUNCTION seg_is_short_structural_fragment(t TEXT)
RETURNS BOOLEAN AS $$
    SELECT core <> ''
       AND length(core) <= 4
       AND (
            core ~ '^[0-9]+[A-Za-z]?$'
            OR core ~ '^[A-Za-z]$'
            OR core ~* '^[ivxlcdm]{1,4}$'
       )
    FROM (
        SELECT regexp_replace(seg_norm_match_text(t), '[^[:alnum:]]+', '', 'g') AS core
    ) x
$$ LANGUAGE sql IMMUTABLE;

-- 与 segment_effective_status_conditions 保持一致：exact / fuzzy / none 三桶。
CREATE OR REPLACE FUNCTION segment_effective_bucket(
    p_source_text TEXT,
    p_display_text TEXT,
    p_matched_source_text TEXT,
    p_status TEXT,
    p_score DOUBLE PRECISION
)
RETURNS TEXT AS $$
DECLARE
    src TEXT := seg_norm_match_text(p_source_text);
    disp TEXT := seg_norm_match_text(p_display_text);
    mat TEXT := seg_norm_match_text(p_matched_source_text);
BEGIN
    IF (src <> '' AND mat <> '' AND mat = src)
       OR (
            disp <> '' AND mat <> '' AND mat = disp
            AND NOT seg_is_short_structural_fragment(p_source_text)
       ) THEN
        RETURN 'exact';
    END IF;
    IF p_status = 'fuzzy' OR coalesce(p_score, 0) > 0 OR mat <> '' THEN
        RETURN 'fuzzy';
    END IF;
    RETURN 'none';
END;
$$ LANGUAGE plpgsql IMMUTABLE;

CREATE OR REPLACE FUNCTION file_segment_stats_on_insert()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO file_segment_stats AS fss (
        file_record_id, total, exact_count, fuzzy_count, none_count,
        confirmed_count, empty_target_count, updated_at
    )
    SELECT
        n.file_record_id,
        count(*),
        count(*) FILTER (WHERE n.bucket = 'exact'),
        count(*) FILTER (WHERE n.bucket = 'fuzzy'),
        count(*) FILTER (WHERE n.bucket = 'none'),
        count(*) FILTER (WHERE n.status = 'confirmed'),
        count(*) FILTER (WHERE coalesce(n.target_text, '') = ''),
        now()
    FROM (
        SELECT file_record_id, status, target_text,
               segment_effective_bucket(source_text, display_text, matched_source_text, status, score) AS bucket
        FROM new_segments
    ) n
    GROUP BY n.file_record_id
    ON CONFLICT (file_record_id) DO UPDATE SET
        total = fss.total + EXCLUDED.total,
        exact_count = fss.exact_count + EXCLUDED.exact_count,
        fuzzy_count = fss.fuzzy_count + EXCLUDED.fuzzy_count,
        none_count = fss.none_count + EXCLUDED.none_count,
        confirmed_count = fss.confirmed_count + EXCLUDED.confirmed_count,
        empty_target_count = fss.empty_target_count + EXCLUDED.empty_target_count,
        updated_at = now();
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION file_segment_stats_on_delete()
RETURNS TRIGGER AS $$
BEGIN
    -- 仅 UPDATE：文件级联删除时统计行本身也会被删除，避免重新插入。
    UPDATE file_segment_stats fss
    SET total = GREATEST(fss.total - d.total, 0),
        exact_count = GREATEST(fss.exact_count - d.exact_count, 0),
        fuzzy_count = GREATEST(fss.fuzzy_count - d.fuzzy_count, 0),
        none_count = GREATEST(fss.none_count - d.none_count, 0),
        confirmed_count = GREATEST(fss.confirmed_count - d.confirmed_count, 0),
        empty_target_count = GREATEST(fss.empty_target_count - d.empty_target_count, 0),
        updated_at = now()
    FROM (
        SELECT
            o.file_record_id,
            count(*) AS total,
            count(*) FILTER (WHERE o.bucket = 'exact') AS exact_count,
            count(*) FILTER (WHERE o.bucket = 'fuzzy') AS fuzzy_count,
            count(*) FILTER (WHERE o.bucket = 'none') AS none_count,
            count(*) FILTER (WHERE o.status = 'confirmed') AS confirmed_count,
            count(*) FILTER (WHERE coalesce(o.target_text, '') = '') AS empty_target_count
        FROM (
            SELECT file_record_id, status, target_text,
                   segment_effective_bucket(source_text, display_text, matched_source_text, status, score) AS bucket
            FROM old_segments
        ) o
        GROUP BY o.file_record_id
    ) d
    WHERE fss.file_record_id = d.file_record_id;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION file_segment_stats_on_update()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE file_segment_stats fss
    SET total = GREATEST(fss.total + d.total, 0),
        exact_count = GREATEST(fss.exact_count + d.exact_count, 0),
        fuzzy_count = GREATEST(fss.fuzzy_count + d.fuzzy_count, 0),
        none_count = GREATEST(fss.none_count + d.none_count, 0),
        confirmed_count = GREATEST(fss.confirmed_count + d.confirmed_count, 0),
        empty_target_count = GREATEST(fss.empty_target_count + d.empty_target_count, 0),
        updated_at = now()
    FROM (
        SELECT
            x.file_record_id,
            SUM(x.sign) AS total,
            SUM(x.sign * (x.bucket = 'exact')::int) AS exact_count,
            SUM(x.sign * (x.bucket = 'fuzzy')::int) AS fuzzy_count,
            SUM(x.sign * (x.bucket = 'none')::int) AS none_count,
            SUM(x.sign * (x.status = 'confirmed')::int) AS confirmed_count,
            SUM(x.sign * (coalesce(x.target_text, '') = '')::int) AS empty_target_count
        FROM (
            SELECT file_record_id, status, target_text, 1 AS sign,
                   segment_effective_bucket(source_text, display_text, matched_source_text, status, score) AS bucket
            FROM new_segments
            UNION ALL
            SELECT file_record_id, status, target_text, -1 AS sign,
                   segment_effective_bucket(source_text, display_text, matched_source_text, status, score) AS bucket
            FROM old_segments
        ) x
        GROUP BY x.file_record_id
    ) d
    WHERE fss.file_record_id = d.file_record_id;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'segments_file_stats_ins_trg'
    ) THEN
        CREATE TRIGGER segments_file_stats_ins_trg
            AFTER INSERT ON segments
            REFERENCING NEW TABLE AS new_segments
            FOR EACH STATEMENT
            EXECUTE FUNCTION file_segment_stats_on_insert();

        CREATE TRIGGER segments_file_stats_del_trg
            AFTER DELETE ON segments
            REFERENCING OLD TABLE AS old_segments
            FOR EACH STATEMENT
            EXECUTE FUNCTION file_segment_stats_on_delete();

        CREATE TRIGGER segments_file_stats_upd_trg
            AFTER UPDATE ON segments
            REFERENCING OLD TABLE AS old_segments NEW TABLE AS new_segments
            FOR EACH STATEMENT
            EXECUTE FUNCTION file_segment_stats_on_update();

        -- 初始回填：与触发器创建同事务，快照一致。
        INSERT INTO file_segment_stats (
            file_record_id, total, exact_count, fuzzy_count, none_count,
            confirmed_count, empty_target_count, updated_at
        )
        SELECT
            s.file_record_id,
            count(*),
            count(*) FILTER (WHERE s.bucket = 'exact'),
            count(*) FILTER (WHERE s.bucket = 'fuzzy'),
            count(*) FILTER (WHERE s.bucket = 'none'),
            count(*) FILTER (WHERE s.status = 'confirmed'),
            count(*) FILTER (WHERE coalesce(s.target_text, '') = ''),
            now()
        FROM (
            SELECT file_record_id, status, target_text,
                   segment_effective_bucket(source_text, display_text, matched_source_text, status, score) AS bucket
            FROM segments
        ) s
        GROUP BY s.file_record_id
        ON CONFLICT (file_record_id) DO NOTHING;
    END IF;
END $$;

COMMIT;
