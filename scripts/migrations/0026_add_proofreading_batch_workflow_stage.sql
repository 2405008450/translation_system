-- 校对批次增加可判定的工作流阶段。可重复执行。
-- not_applicable / import / alignment / proofreading

ALTER TABLE proofreading_batches
    ADD COLUMN IF NOT EXISTS workflow_stage VARCHAR(20) NOT NULL DEFAULT 'not_applicable';

UPDATE proofreading_batches
SET workflow_stage = CASE
    WHEN batch_kind = 'xlsx_columns' THEN 'proofreading'
    WHEN batch_kind = 'document_pair' AND alignment_status = 'confirmed'
        AND status IN ('queued', 'running', 'canceling', 'completed', 'partial_failed') THEN 'proofreading'
    WHEN batch_kind = 'document_pair' AND alignment_status = 'confirmed' THEN 'alignment'
    WHEN batch_kind = 'document_pair' THEN 'import'
    ELSE workflow_stage
END
WHERE workflow_stage = 'not_applicable';
