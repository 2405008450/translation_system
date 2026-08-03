-- 为样式专检报告增加合并视图归属，支持跨文件报告查询和定位。
ALTER TABLE IF EXISTS style_tag_check_reports
    ADD COLUMN IF NOT EXISTS merge_view_id UUID NULL
    REFERENCES project_merge_views(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS ix_style_tag_check_reports_merge_view_id
    ON style_tag_check_reports (merge_view_id);
