CREATE TABLE IF NOT EXISTS revision_display_settings (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    file_record_id UUID NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
    show_author_time BOOLEAN NOT NULL DEFAULT TRUE,
    show_others_revisions BOOLEAN NOT NULL DEFAULT TRUE,
    default_insert_color VARCHAR(20) NOT NULL DEFAULT '#2563eb',
    default_delete_color VARCHAR(20) NOT NULL DEFAULT '#dc2626',
    author_colors JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_revision_display_settings_file_record_id UNIQUE (file_record_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_revision_display_settings_file_record_id
    ON revision_display_settings (file_record_id);
CREATE INDEX IF NOT EXISTS ix_revision_display_settings_updated_by_id
    ON revision_display_settings (updated_by_id);

ALTER TABLE IF EXISTS revision_display_settings
    ADD COLUMN IF NOT EXISTS show_author_time BOOLEAN;
ALTER TABLE IF EXISTS revision_display_settings
    ADD COLUMN IF NOT EXISTS show_others_revisions BOOLEAN;
ALTER TABLE IF EXISTS revision_display_settings
    ADD COLUMN IF NOT EXISTS default_insert_color VARCHAR(20);
ALTER TABLE IF EXISTS revision_display_settings
    ADD COLUMN IF NOT EXISTS default_delete_color VARCHAR(20);
ALTER TABLE IF EXISTS revision_display_settings
    ADD COLUMN IF NOT EXISTS author_colors JSONB;
ALTER TABLE IF EXISTS revision_display_settings
    ADD COLUMN IF NOT EXISTS updated_by_id UUID;
ALTER TABLE IF EXISTS revision_display_settings
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW();

ALTER TABLE revision_display_settings
    ALTER COLUMN show_author_time SET DEFAULT TRUE;
ALTER TABLE revision_display_settings
    ALTER COLUMN show_others_revisions SET DEFAULT TRUE;
ALTER TABLE revision_display_settings
    ALTER COLUMN default_insert_color SET DEFAULT '#2563eb';
ALTER TABLE revision_display_settings
    ALTER COLUMN default_delete_color SET DEFAULT '#dc2626';
ALTER TABLE revision_display_settings
    ALTER COLUMN author_colors SET DEFAULT '{}'::jsonb;
ALTER TABLE revision_display_settings
    ALTER COLUMN updated_at SET DEFAULT NOW();

UPDATE revision_display_settings
SET show_author_time = COALESCE(show_author_time, TRUE),
    show_others_revisions = COALESCE(show_others_revisions, TRUE),
    default_insert_color = COALESCE(NULLIF(default_insert_color, ''), '#2563eb'),
    default_delete_color = COALESCE(NULLIF(default_delete_color, ''), '#dc2626'),
    author_colors = COALESCE(author_colors, '{}'::jsonb),
    updated_at = COALESCE(updated_at, NOW())
WHERE show_author_time IS NULL
   OR show_others_revisions IS NULL
   OR default_insert_color IS NULL
   OR btrim(default_insert_color) = ''
   OR default_delete_color IS NULL
   OR btrim(default_delete_color) = ''
   OR author_colors IS NULL
   OR updated_at IS NULL;
