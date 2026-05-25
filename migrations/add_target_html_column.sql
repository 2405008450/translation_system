-- 添加 target_html 字段用于存储带格式的译文
-- 如果 target_html 为空或只包含纯文本，导出时继承原文格式
-- 如果 target_html 包含格式标签（<b>, <i>, <u>, <s>, <sub>, <sup>），导出时使用译文格式

ALTER TABLE segments ADD COLUMN IF NOT EXISTS target_html TEXT;

-- 添加索引以便快速查询有格式的译文
CREATE INDEX IF NOT EXISTS idx_segments_has_target_html ON segments ((target_html IS NOT NULL AND target_html != ''));

COMMENT ON COLUMN segments.target_html IS '带格式的译文HTML，支持 <b>, <i>, <u>, <s>, <sub>, <sup> 标签';
