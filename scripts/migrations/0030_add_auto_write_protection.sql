ALTER TABLE segments
    ADD COLUMN IF NOT EXISTS auto_write_protection VARCHAR(20) NOT NULL DEFAULT 'none',
    ADD COLUMN IF NOT EXISTS auto_write_protection_reason VARCHAR(40),
    ADD COLUMN IF NOT EXISTS auto_write_protection_updated_at TIMESTAMP WITHOUT TIME ZONE,
    ADD COLUMN IF NOT EXISTS auto_write_protection_updated_by_id UUID REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE pretranslation_tasks
    ADD COLUMN IF NOT EXISTS skipped_protected_segments INTEGER NOT NULL DEFAULT 0;
