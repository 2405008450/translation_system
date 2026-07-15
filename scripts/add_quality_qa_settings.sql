-- 拼写/语法 QA 自动检查配置与句段级问题表

ALTER TABLE projects
  ADD COLUMN IF NOT EXISTS quality_qa_settings TEXT NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS segment_qa_issues (
  id UUID PRIMARY KEY DEFAULT (
    lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
    lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
    '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
    substr('89ab', floor(random() * 4)::int + 1, 1) ||
    substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
    lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
  )::uuid,
  project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
  file_record_id UUID NOT NULL REFERENCES file_records(id) ON DELETE CASCADE,
  segment_id UUID NOT NULL REFERENCES segments(id) ON DELETE CASCADE,
  sentence_id VARCHAR(100) NOT NULL DEFAULT '',
  rule_key VARCHAR(40) NOT NULL DEFAULT 'spelling_grammar',
  provider VARCHAR(40) NOT NULL DEFAULT 'languagetool',
  language VARCHAR(20) NOT NULL DEFAULT '',
  severity VARCHAR(20) NOT NULL DEFAULT 'medium',
  message TEXT NOT NULL DEFAULT '',
  short_message TEXT NOT NULL DEFAULT '',
  rule_id VARCHAR(120) NOT NULL DEFAULT '',
  rule_category VARCHAR(120) NOT NULL DEFAULT '',
  issue_type VARCHAR(80) NOT NULL DEFAULT '',
  context_text TEXT NOT NULL DEFAULT '',
  "offset" INTEGER NOT NULL DEFAULT 0,
  length INTEGER NOT NULL DEFAULT 0,
  replacements TEXT NOT NULL DEFAULT '[]',
  target_text_hash VARCHAR(64) NOT NULL DEFAULT '',
  status VARCHAR(20) NOT NULL DEFAULT 'open',
  ignored_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
  ignored_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE segment_qa_issues
  ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
  ADD COLUMN IF NOT EXISTS file_record_id UUID REFERENCES file_records(id) ON DELETE CASCADE,
  ADD COLUMN IF NOT EXISTS segment_id UUID REFERENCES segments(id) ON DELETE CASCADE,
  ADD COLUMN IF NOT EXISTS sentence_id VARCHAR(100) NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS rule_key VARCHAR(40) NOT NULL DEFAULT 'spelling_grammar',
  ADD COLUMN IF NOT EXISTS provider VARCHAR(40) NOT NULL DEFAULT 'languagetool',
  ADD COLUMN IF NOT EXISTS language VARCHAR(20) NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS severity VARCHAR(20) NOT NULL DEFAULT 'medium',
  ADD COLUMN IF NOT EXISTS message TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS short_message TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS rule_id VARCHAR(120) NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS rule_category VARCHAR(120) NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS issue_type VARCHAR(80) NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS context_text TEXT NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS "offset" INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS length INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS replacements TEXT NOT NULL DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS target_text_hash VARCHAR(64) NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'open',
  ADD COLUMN IF NOT EXISTS ignored_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS ignored_at TIMESTAMP,
  ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT NOW();

CREATE INDEX IF NOT EXISTS ix_segment_qa_issues_project_id
  ON segment_qa_issues (project_id);
CREATE INDEX IF NOT EXISTS ix_segment_qa_issues_file_record_id
  ON segment_qa_issues (file_record_id);
CREATE INDEX IF NOT EXISTS ix_segment_qa_issues_segment_id
  ON segment_qa_issues (segment_id);
CREATE INDEX IF NOT EXISTS ix_segment_qa_issues_segment_rule_status
  ON segment_qa_issues (segment_id, rule_key, status);
CREATE INDEX IF NOT EXISTS ix_segment_qa_issues_status
  ON segment_qa_issues (status);
CREATE INDEX IF NOT EXISTS ix_segment_qa_issues_rule_key
  ON segment_qa_issues (rule_key);
CREATE INDEX IF NOT EXISTS ix_segment_qa_issues_target_hash
  ON segment_qa_issues (target_text_hash);

DROP TRIGGER IF EXISTS update_segment_qa_issues_updated_at ON segment_qa_issues;
CREATE TRIGGER update_segment_qa_issues_updated_at
  BEFORE UPDATE ON segment_qa_issues
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();
