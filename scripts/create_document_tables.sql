<<<<<<< HEAD
CREATE TABLE IF NOT EXISTS file_records (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
=======
-- 创建文档表和片段表
-- 执行前请确认连接的是正确的数据库

-- 文档表
CREATE TABLE IF NOT EXISTS file_records (
    id BIGSERIAL PRIMARY KEY,
>>>>>>> 506e4e1 (移除 __pycache__ 追踪)
    filename VARCHAR(255) NOT NULL,
    file_hash VARCHAR(64),
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS segments (
<<<<<<< HEAD
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    file_record_id UUID NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
=======
    id BIGSERIAL PRIMARY KEY,
    file_record_id BIGINT NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
>>>>>>> 506e4e1 (移除 __pycache__ 追踪)
    sentence_id VARCHAR(20) NOT NULL,
    source_text TEXT NOT NULL,
    display_text TEXT NOT NULL,
    target_text TEXT NOT NULL DEFAULT '',
    status VARCHAR(20) NOT NULL DEFAULT 'none',
    score FLOAT NOT NULL DEFAULT 0.0,
    matched_source_text TEXT,
    source VARCHAR(20) NOT NULL DEFAULT 'tm',
    block_type VARCHAR(20) NOT NULL DEFAULT 'paragraph',
    block_index INTEGER NOT NULL DEFAULT 0,
    row_index INTEGER,
    cell_index INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

<<<<<<< HEAD
CREATE INDEX IF NOT EXISTS ix_segments_file_record_id
    ON segments (file_record_id);
=======
-- 索引
CREATE INDEX IF NOT EXISTS ix_segments_file_record_id ON segments(file_record_id);
>>>>>>> 506e4e1 (移除 __pycache__ 追踪)

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_file_records_updated_at ON file_records;
CREATE TRIGGER update_file_records_updated_at
    BEFORE UPDATE ON file_records
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_segments_updated_at ON segments;
CREATE TRIGGER update_segments_updated_at
    BEFORE UPDATE ON segments
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
