-- 只读审计：列出当前为 project_sync、但最近一次人工修订与当前译文不同的疑似历史覆盖记录。
-- 用法：psql "$DATABASE_URL" -v project_id='项目 UUID' --csv -f scripts/audit_project_sync_overwrites.sql > audit.csv

SELECT
    p.id AS project_id,
    fr.id AS file_record_id,
    fr.filename,
    s.id AS segment_id,
    s.sentence_id,
    s.source_text,
    manual_revision.after_text AS latest_manual_text,
    manual_revision.created_at AS latest_manual_at,
    s.target_text AS current_target_text,
    s.updated_at AS current_updated_at,
    s.project_sync_source_segment_id,
    s.project_sync_source_file_record_id
FROM segments AS s
JOIN file_records AS fr ON fr.id = s.file_record_id
JOIN projects AS p ON p.id = fr.project_id
JOIN LATERAL (
    SELECT sr.after_text, sr.created_at
    FROM segment_revisions AS sr
    WHERE sr.segment_id = s.id
      AND sr.source = 'manual'
    ORDER BY sr.created_at DESC, sr.id DESC
    LIMIT 1
) AS manual_revision ON TRUE
WHERE p.id = :'project_id'::uuid
  AND s.source = 'project_sync'
  AND BTRIM(COALESCE(manual_revision.after_text, '')) <> BTRIM(COALESCE(s.target_text, ''))
ORDER BY manual_revision.created_at DESC, fr.filename, s.sentence_id;
