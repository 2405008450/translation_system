-- 双文档对齐中间态。所有语句均可重复执行。
ALTER TABLE proofreading_batches ADD COLUMN IF NOT EXISTS batch_kind VARCHAR(30) NOT NULL DEFAULT 'xlsx_columns';
ALTER TABLE proofreading_batches ADD COLUMN IF NOT EXISTS alignment_status VARCHAR(20) NOT NULL DEFAULT 'not_applicable';
ALTER TABLE proofreading_batches ADD COLUMN IF NOT EXISTS target_language VARCHAR(20) NOT NULL DEFAULT '';
CREATE TABLE IF NOT EXISTS document_alignment_pairs (
  id UUID PRIMARY KEY DEFAULT ((lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' || lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' || substr('89ab', floor(random() * 4)::int + 1, 1) || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' || lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0'))::uuid),
  batch_id UUID NOT NULL REFERENCES proofreading_batches(id) ON DELETE CASCADE,
  pair_order INTEGER NOT NULL, src_indices TEXT NOT NULL DEFAULT '[]', tgt_indices TEXT NOT NULL DEFAULT '[]',
  source_text TEXT NOT NULL DEFAULT '', target_text TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL DEFAULT 0,
  confidence_level VARCHAR(10) NOT NULL DEFAULT 'medium', method VARCHAR(30) NOT NULL DEFAULT 'dp',
  features TEXT NOT NULL DEFAULT '{}', locked BOOLEAN NOT NULL DEFAULT FALSE,
  block_type VARCHAR(30) NOT NULL DEFAULT 'paragraph', block_index INTEGER NOT NULL DEFAULT 0,
  row_index INTEGER, cell_index INTEGER, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_alignment_pair_order UNIQUE (batch_id, pair_order)
);

CREATE TABLE IF NOT EXISTS document_alignment_units (
  id UUID PRIMARY KEY DEFAULT ((lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' || lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' || substr('89ab', floor(random() * 4)::int + 1, 1) || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' || lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0'))::uuid),
  batch_id UUID NOT NULL REFERENCES proofreading_batches(id) ON DELETE CASCADE,
  side VARCHAR(10) NOT NULL, unit_index INTEGER NOT NULL, text TEXT NOT NULL DEFAULT '',
  para_index INTEGER NOT NULL DEFAULT 0, block_type VARCHAR(30) NOT NULL DEFAULT 'paragraph',
  block_index INTEGER NOT NULL DEFAULT 0, row_index INTEGER, cell_index INTEGER,
  numbering VARCHAR(60) NOT NULL DEFAULT '', CONSTRAINT uq_alignment_unit UNIQUE (batch_id, side, unit_index)
);

CREATE INDEX IF NOT EXISTS ix_document_alignment_pairs_batch_id ON document_alignment_pairs(batch_id);
CREATE INDEX IF NOT EXISTS ix_document_alignment_pairs_confidence ON document_alignment_pairs(batch_id, confidence_level);
CREATE INDEX IF NOT EXISTS ix_document_alignment_units_batch_side ON document_alignment_units(batch_id, side, unit_index);
CREATE INDEX IF NOT EXISTS ix_proofreading_batches_batch_kind ON proofreading_batches(batch_kind);
