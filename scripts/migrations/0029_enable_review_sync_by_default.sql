BEGIN;
DO $$
BEGIN
    LOCK TABLE projects IN ACCESS EXCLUSIVE MODE;
    IF COALESCE((
        SELECT pg_get_expr(d.adbin, d.adrelid)
        FROM pg_attribute a
        LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
        WHERE a.attrelid = 'projects'::regclass AND a.attname = 'review_sync_enabled'
    ), '') <> 'true' THEN
        ALTER TABLE projects ALTER COLUMN review_sync_enabled SET DEFAULT TRUE;
        UPDATE projects SET review_sync_enabled = TRUE WHERE review_sync_enabled = FALSE;
    END IF;
END $$;
COMMIT;
