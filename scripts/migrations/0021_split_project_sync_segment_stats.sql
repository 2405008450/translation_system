-- 将项目内重复句段同步从“精确匹配”统计中拆出为独立桶。
-- Segment.status 保持兼容；以 segments.source = 'project_sync' 作为权威判定。

BEGIN;

ALTER TABLE file_segment_stats
    ADD COLUMN IF NOT EXISTS project_sync_count INTEGER NOT NULL DEFAULT 0;

CREATE OR REPLACE FUNCTION file_segment_stats_on_insert()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO file_segment_stats AS fss (
        file_record_id, total, project_sync_count, exact_count, fuzzy_count, none_count,
        confirmed_count, empty_target_count, updated_at
    )
    SELECT
        n.file_record_id,
        count(*),
        count(*) FILTER (WHERE n.is_project_sync),
        count(*) FILTER (WHERE NOT n.is_project_sync AND n.bucket = 'exact'),
        count(*) FILTER (WHERE NOT n.is_project_sync AND n.bucket = 'fuzzy'),
        count(*) FILTER (WHERE NOT n.is_project_sync AND n.bucket = 'none'),
        count(*) FILTER (WHERE n.status = 'confirmed'),
        count(*) FILTER (WHERE coalesce(n.target_text, '') = ''),
        now()
    FROM (
        SELECT file_record_id, status, target_text,
               coalesce(source, '') = 'project_sync' AS is_project_sync,
               segment_effective_bucket(source_text, display_text, matched_source_text, status, score) AS bucket
        FROM new_segments
    ) n
    GROUP BY n.file_record_id
    ON CONFLICT (file_record_id) DO UPDATE SET
        total = fss.total + EXCLUDED.total,
        project_sync_count = fss.project_sync_count + EXCLUDED.project_sync_count,
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
        project_sync_count = GREATEST(fss.project_sync_count - d.project_sync_count, 0),
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
            count(*) FILTER (WHERE o.is_project_sync) AS project_sync_count,
            count(*) FILTER (WHERE NOT o.is_project_sync AND o.bucket = 'exact') AS exact_count,
            count(*) FILTER (WHERE NOT o.is_project_sync AND o.bucket = 'fuzzy') AS fuzzy_count,
            count(*) FILTER (WHERE NOT o.is_project_sync AND o.bucket = 'none') AS none_count,
            count(*) FILTER (WHERE o.status = 'confirmed') AS confirmed_count,
            count(*) FILTER (WHERE coalesce(o.target_text, '') = '') AS empty_target_count
        FROM (
            SELECT file_record_id, status, target_text,
                   coalesce(source, '') = 'project_sync' AS is_project_sync,
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
        project_sync_count = GREATEST(fss.project_sync_count + d.project_sync_count, 0),
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
            SUM(x.sign * x.is_project_sync::int) AS project_sync_count,
            SUM(x.sign * (NOT x.is_project_sync AND x.bucket = 'exact')::int) AS exact_count,
            SUM(x.sign * (NOT x.is_project_sync AND x.bucket = 'fuzzy')::int) AS fuzzy_count,
            SUM(x.sign * (NOT x.is_project_sync AND x.bucket = 'none')::int) AS none_count,
            SUM(x.sign * (x.status = 'confirmed')::int) AS confirmed_count,
            SUM(x.sign * (coalesce(x.target_text, '') = '')::int) AS empty_target_count
        FROM (
            SELECT file_record_id, status, target_text, 1 AS sign,
                   coalesce(source, '') = 'project_sync' AS is_project_sync,
                   segment_effective_bucket(source_text, display_text, matched_source_text, status, score) AS bucket
            FROM new_segments
            UNION ALL
            SELECT file_record_id, status, target_text, -1 AS sign,
                   coalesce(source, '') = 'project_sync' AS is_project_sync,
                   segment_effective_bucket(source_text, display_text, matched_source_text, status, score) AS bucket
            FROM old_segments
        ) x
        GROUP BY x.file_record_id
    ) d
    WHERE fss.file_record_id = d.file_record_id;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- 回填历史数据，并修正过去被计入 exact_count 的项目同步句段。
INSERT INTO file_segment_stats AS fss (
    file_record_id, total, project_sync_count, exact_count, fuzzy_count, none_count,
    confirmed_count, empty_target_count, updated_at
)
SELECT
    s.file_record_id,
    count(*),
    count(*) FILTER (WHERE s.is_project_sync),
    count(*) FILTER (WHERE NOT s.is_project_sync AND s.bucket = 'exact'),
    count(*) FILTER (WHERE NOT s.is_project_sync AND s.bucket = 'fuzzy'),
    count(*) FILTER (WHERE NOT s.is_project_sync AND s.bucket = 'none'),
    count(*) FILTER (WHERE s.status = 'confirmed'),
    count(*) FILTER (WHERE coalesce(s.target_text, '') = ''),
    now()
FROM (
    SELECT file_record_id, status, target_text,
           coalesce(source, '') = 'project_sync' AS is_project_sync,
           segment_effective_bucket(source_text, display_text, matched_source_text, status, score) AS bucket
    FROM segments
) s
GROUP BY s.file_record_id
ON CONFLICT (file_record_id) DO UPDATE SET
    total = EXCLUDED.total,
    project_sync_count = EXCLUDED.project_sync_count,
    exact_count = EXCLUDED.exact_count,
    fuzzy_count = EXCLUDED.fuzzy_count,
    none_count = EXCLUDED.none_count,
    confirmed_count = EXCLUDED.confirmed_count,
    empty_target_count = EXCLUDED.empty_target_count,
    updated_at = now();

COMMIT;
