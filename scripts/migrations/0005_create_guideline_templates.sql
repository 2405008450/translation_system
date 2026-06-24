CREATE TABLE IF NOT EXISTS guideline_templates (
    id VARCHAR(120) PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    filename VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    source_path VARCHAR(255),
    created_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    last_modified_by_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

ALTER TABLE guideline_templates
    ADD COLUMN IF NOT EXISTS name VARCHAR(120) NOT NULL DEFAULT '';

ALTER TABLE guideline_templates
    ADD COLUMN IF NOT EXISTS filename VARCHAR(255) NOT NULL DEFAULT '';

ALTER TABLE guideline_templates
    ADD COLUMN IF NOT EXISTS content TEXT NOT NULL DEFAULT '';

ALTER TABLE guideline_templates
    ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64) NOT NULL DEFAULT '';

ALTER TABLE guideline_templates
    ADD COLUMN IF NOT EXISTS size_bytes INTEGER NOT NULL DEFAULT 0;

ALTER TABLE guideline_templates
    ADD COLUMN IF NOT EXISTS source_path VARCHAR(255);

ALTER TABLE guideline_templates
    ADD COLUMN IF NOT EXISTS created_by_id UUID REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE guideline_templates
    ADD COLUMN IF NOT EXISTS last_modified_by_id UUID REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE guideline_templates
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW();

ALTER TABLE guideline_templates
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP NOT NULL DEFAULT NOW();

CREATE INDEX IF NOT EXISTS ix_guideline_templates_updated_at
    ON guideline_templates (updated_at);

CREATE INDEX IF NOT EXISTS ix_guideline_templates_created_by_id
    ON guideline_templates (created_by_id);

CREATE INDEX IF NOT EXISTS ix_guideline_templates_last_modified_by_id
    ON guideline_templates (last_modified_by_id);

DROP TRIGGER IF EXISTS update_guideline_templates_updated_at ON guideline_templates;

CREATE TRIGGER update_guideline_templates_updated_at
    BEFORE UPDATE ON guideline_templates
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
