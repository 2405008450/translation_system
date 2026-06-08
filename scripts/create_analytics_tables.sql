-- 数据看板统计结构。
-- 可手动执行；服务启动时的 runtime schema 也会自动补齐这些结构。

ALTER TABLE IF EXISTS segments
ADD COLUMN IF NOT EXISTS source_word_count INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS ix_segments_source_word_count
ON segments (source_word_count);

CREATE INDEX IF NOT EXISTS ix_segments_translated_source_word_count
ON segments (file_record_id, source_word_count)
WHERE target_text <> '' AND source_word_count > 0;

CREATE INDEX IF NOT EXISTS ix_segments_source_word_backfill
ON segments (id)
WHERE source_word_count = 0 AND source_text <> '';

CREATE INDEX IF NOT EXISTS ix_segments_translated_backfill
ON segments (updated_at, id)
WHERE target_text <> '' AND source_word_count > 0;

CREATE TABLE IF NOT EXISTS translation_metric_events (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    event_key VARCHAR(140),
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    file_record_id UUID REFERENCES file_records(id) ON DELETE SET NULL,
    segment_id UUID REFERENCES segments(id) ON DELETE SET NULL,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    source VARCHAR(20) NOT NULL DEFAULT 'manual',
    source_language VARCHAR(20),
    target_language VARCHAR(20),
    source_word_count INTEGER NOT NULL DEFAULT 0,
    target_was_empty BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_translation_metric_events_event_key
ON translation_metric_events (event_key)
WHERE event_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_translation_metric_events_created_at
ON translation_metric_events (created_at);

CREATE INDEX IF NOT EXISTS ix_translation_metric_events_source
ON translation_metric_events (source);

CREATE INDEX IF NOT EXISTS ix_translation_metric_events_language_pair
ON translation_metric_events (source_language, target_language);

CREATE INDEX IF NOT EXISTS ix_translation_metric_events_file_record_id
ON translation_metric_events (file_record_id);

CREATE INDEX IF NOT EXISTS ix_translation_metric_events_segment_id
ON translation_metric_events (segment_id);

CREATE INDEX IF NOT EXISTS ix_translation_metric_events_source_created_at
ON translation_metric_events (source, created_at);

CREATE TABLE IF NOT EXISTS user_activity_daily (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    activity_date DATE NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    first_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_user_activity_daily_user_date
ON user_activity_daily (user_id, activity_date);

CREATE INDEX IF NOT EXISTS ix_user_activity_daily_activity_date
ON user_activity_daily (activity_date);

CREATE INDEX IF NOT EXISTS ix_user_activity_daily_user_id
ON user_activity_daily (user_id);
