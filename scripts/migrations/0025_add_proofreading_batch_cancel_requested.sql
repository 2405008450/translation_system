-- 校对批次支持协作取消：记录取消请求标志。
-- 可重复执行。

ALTER TABLE proofreading_batches
    ADD COLUMN IF NOT EXISTS cancel_requested BOOLEAN NOT NULL DEFAULT FALSE;
