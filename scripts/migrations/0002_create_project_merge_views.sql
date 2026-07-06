-- 项目"合并视图"：记录同一项目中哪些 file_records 组成一个编辑视图。
-- 仅持久化分组关系，不为合并单独存储句段——句段仍归属各自 file_record，
-- 保存/导出复用按文件现有的接口。
-- 幂等：每次容器启动由 run_migrations.sh 重复执行也安全。

CREATE TABLE IF NOT EXISTS project_merge_views (
    id UUID PRIMARY KEY DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name VARCHAR(200) NOT NULL,
    -- 有序 file_record UUID 数组（JSON 文本，与 collection_ids_json 等列保持一致风格）
    file_ids TEXT NOT NULL DEFAULT '[]',
    creator_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_project_merge_views_project_id
    ON project_merge_views (project_id);

CREATE INDEX IF NOT EXISTS ix_project_merge_views_creator_id
    ON project_merge_views (creator_id);

DROP TRIGGER IF EXISTS update_project_merge_views_updated_at ON project_merge_views;

CREATE TRIGGER update_project_merge_views_updated_at
    BEFORE UPDATE ON project_merge_views
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
