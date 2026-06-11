-- 添加 source_html 字段，用于保存导入时提取到的原文局部样式 HTML。
-- 为空时前端会退回使用 display_text/source_text 的纯文本渲染。

ALTER TABLE segments ADD COLUMN IF NOT EXISTS source_html TEXT;

COMMENT ON COLUMN segments.source_html IS '带格式的原文片段 HTML，用于工作台源文列显示导入文档中的局部样式';
