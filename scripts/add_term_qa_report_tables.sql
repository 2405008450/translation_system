-- 项目术语库配置与术语 QA 报告

ALTER TABLE file_records
  ADD COLUMN IF NOT EXISTS term_base_write_ids TEXT NOT NULL DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS qa_term_base_ids TEXT NOT NULL DEFAULT '[]';

CREATE TABLE IF NOT EXISTS term_qa_reports (
  id UUID PRIMARY KEY DEFAULT (
    lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
    lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
    '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
    substr('89ab', floor(random() * 4)::int + 1, 1) ||
    substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
    lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
  )::uuid,
  project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
  file_record_id UUID REFERENCES file_records(id) ON DELETE CASCADE,
  created_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
  scope VARCHAR(20) NOT NULL DEFAULT 'project',
  file_ids TEXT NOT NULL DEFAULT '[]',
  term_base_ids TEXT NOT NULL DEFAULT '[]',
  language_pairs TEXT NOT NULL DEFAULT '[]',
  total_files INTEGER NOT NULL DEFAULT 0,
  total_segments INTEGER NOT NULL DEFAULT 0,
  checked_segments INTEGER NOT NULL DEFAULT 0,
  issue_count INTEGER NOT NULL DEFAULT 0,
  status VARCHAR(20) NOT NULL DEFAULT 'completed',
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS term_qa_report_items (
  id UUID PRIMARY KEY DEFAULT (
    lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
    lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
    '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
    substr('89ab', floor(random() * 4)::int + 1, 1) ||
    substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
    lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
  )::uuid,
  report_id UUID NOT NULL REFERENCES term_qa_reports(id) ON DELETE CASCADE,
  project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
  file_record_id UUID NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
  segment_id UUID REFERENCES segments(id) ON DELETE SET NULL,
  term_base_id UUID REFERENCES term_bases(id) ON DELETE SET NULL,
  sentence_id VARCHAR(100) NOT NULL DEFAULT '',
  file_name VARCHAR(255) NOT NULL DEFAULT '',
  term_base_name VARCHAR(200) NOT NULL DEFAULT '',
  source_term TEXT NOT NULL,
  expected_target_term TEXT NOT NULL,
  source_text TEXT NOT NULL,
  target_text TEXT NOT NULL DEFAULT '',
  block_index INTEGER NOT NULL DEFAULT 0,
  row_index INTEGER,
  cell_index INTEGER,
  ignored_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
  ignored_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE term_qa_report_items
  ADD COLUMN IF NOT EXISTS ignored_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS ignored_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS ix_term_qa_reports_project_id
  ON term_qa_reports (project_id);
CREATE INDEX IF NOT EXISTS ix_term_qa_reports_file_record_id
  ON term_qa_reports (file_record_id);
CREATE INDEX IF NOT EXISTS ix_term_qa_reports_created_by_id
  ON term_qa_reports (created_by_id);
CREATE INDEX IF NOT EXISTS ix_term_qa_reports_created_at
  ON term_qa_reports (created_at);
CREATE INDEX IF NOT EXISTS ix_term_qa_report_items_report_id
  ON term_qa_report_items (report_id);
CREATE INDEX IF NOT EXISTS ix_term_qa_report_items_project_id
  ON term_qa_report_items (project_id);
CREATE INDEX IF NOT EXISTS ix_term_qa_report_items_file_record_id
  ON term_qa_report_items (file_record_id);
CREATE INDEX IF NOT EXISTS ix_term_qa_report_items_segment_id
  ON term_qa_report_items (segment_id);
CREATE INDEX IF NOT EXISTS ix_term_qa_report_items_term_base_id
  ON term_qa_report_items (term_base_id);
CREATE INDEX IF NOT EXISTS ix_term_qa_report_items_ignored_at
  ON term_qa_report_items (ignored_at);
