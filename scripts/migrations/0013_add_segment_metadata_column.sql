-- 保存 CAD/DWG/DXF 实体信息及手动合并状态，与 Segment.segment_metadata 模型一致。
ALTER TABLE IF EXISTS segments
    ADD COLUMN IF NOT EXISTS segment_metadata TEXT NOT NULL DEFAULT '{}';

COMMENT ON COLUMN segments.segment_metadata IS
    '句段扩展元数据 JSON，包含 CAD 实体及手动合并信息';
