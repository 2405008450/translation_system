-- 为 TM 条目添加创建者字段
ALTER TABLE memory_entries
  ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL;

-- 为术语条目添加创建者字段
ALTER TABLE term_entries
  ADD COLUMN IF NOT EXISTS creator_id UUID REFERENCES users(id) ON DELETE SET NULL;
