-- 为 file_records 添加绑定的记忆库和术语库
ALTER TABLE file_records
  ADD COLUMN IF NOT EXISTS collection_id UUID REFERENCES memory_bases(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS term_base_id UUID REFERENCES term_bases(id) ON DELETE SET NULL;

-- 为 segments 添加匹配来源记忆库名称
ALTER TABLE segments
  ADD COLUMN IF NOT EXISTS matched_collection_name VARCHAR(120);

-- 为 segments 添加匹配创建者信息
ALTER TABLE segments
  ADD COLUMN IF NOT EXISTS matched_creator_name VARCHAR(100),
  ADD COLUMN IF NOT EXISTS matched_created_at TIMESTAMP,
  ADD COLUMN IF NOT EXISTS matched_updated_at TIMESTAMP;
