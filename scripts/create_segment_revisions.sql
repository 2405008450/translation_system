CREATE TABLE IF NOT EXISTS segment_revisions (
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
    sentence_id VARCHAR(20) NOT NULL,
    before_text TEXT NOT NULL DEFAULT '',
    after_text TEXT NOT NULL DEFAULT '',
    source VARCHAR(20) NOT NULL DEFAULT 'manual',
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    author_id UUID REFERENCES users(id) ON DELETE SET NULL,
    resolved_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_segment_revisions_file_record_id
    ON segment_revisions (file_record_id);
CREATE INDEX IF NOT EXISTS ix_segment_revisions_segment_id
    ON segment_revisions (segment_id);
CREATE INDEX IF NOT EXISTS ix_segment_revisions_sentence_id
    ON segment_revisions (sentence_id);
CREATE INDEX IF NOT EXISTS ix_segment_revisions_status
    ON segment_revisions (status);
