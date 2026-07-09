-- 自动记忆库同步与多人协作版本字段

ALTER TABLE IF EXISTS segments
ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;

WITH ranked_entries AS (
    SELECT
        id,
        ROW_NUMBER() OVER (
            PARTITION BY collection_id, source_hash, source_language, target_language
            ORDER BY updated_at DESC, created_at DESC, id DESC
        ) AS row_number
    FROM memory_entries
    WHERE collection_id IS NOT NULL
      AND source_hash IS NOT NULL
      AND source_language IS NOT NULL
      AND target_language IS NOT NULL
)
DELETE FROM memory_entries
WHERE id IN (
    SELECT id
    FROM ranked_entries
    WHERE row_number > 1
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_entries_collection_source_hash_language_pair
ON memory_entries (collection_id, source_hash, source_language, target_language);

CREATE TABLE IF NOT EXISTS auto_tm_outbox (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    file_record_id UUID NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
    segment_id UUID NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
    sentence_id VARCHAR(100) NOT NULL,
    collection_id UUID NOT NULL REFERENCES memory_bases(id) ON DELETE CASCADE,
    source_text TEXT NOT NULL,
    target_text TEXT NOT NULL,
    source_language VARCHAR(20) NOT NULL,
    target_language VARCHAR(20) NOT NULL,
    creator_id UUID REFERENCES users(id) ON DELETE SET NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT NOT NULL DEFAULT '',
    last_enqueued_at TIMESTAMP NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_auto_tm_outbox_file_segment_collection
ON auto_tm_outbox (file_record_id, segment_id, collection_id);

CREATE INDEX IF NOT EXISTS ix_auto_tm_outbox_status_created_at
ON auto_tm_outbox (status, created_at);

CREATE INDEX IF NOT EXISTS ix_auto_tm_outbox_file_record_id
ON auto_tm_outbox (file_record_id);

CREATE TABLE IF NOT EXISTS auto_tm_rematch_queue (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    file_record_id UUID NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
    collection_id UUID NOT NULL REFERENCES memory_bases(id) ON DELETE CASCADE,
    pending_entry_count INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    first_pending_at TIMESTAMP,
    last_pending_at TIMESTAMP,
    last_processed_at TIMESTAMP,
    error_message TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_auto_tm_rematch_queue_file_record
ON auto_tm_rematch_queue (file_record_id);

CREATE INDEX IF NOT EXISTS ix_auto_tm_rematch_queue_status
ON auto_tm_rematch_queue (status);

CREATE INDEX IF NOT EXISTS ix_auto_tm_rematch_queue_first_pending_at
ON auto_tm_rematch_queue (first_pending_at);
