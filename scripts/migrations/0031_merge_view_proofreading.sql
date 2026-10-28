ALTER TABLE proofreading_segment_baselines ADD COLUMN IF NOT EXISTS reviewed_fingerprint VARCHAR(64);
ALTER TABLE proofreading_segment_baselines ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMP;

CREATE TABLE IF NOT EXISTS merge_proofreading_runs (
	id UUID DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid NOT NULL, 
	view_id UUID NOT NULL, 
	created_by_id UUID NOT NULL, 
	kind VARCHAR(20) NOT NULL, 
	status VARCHAR(24) NOT NULL, 
	options_json TEXT NOT NULL, 
	cancel_requested BOOLEAN NOT NULL, 
	result_path TEXT, 
	created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(view_id) REFERENCES project_merge_views (id) ON DELETE CASCADE, 
	FOREIGN KEY(created_by_id) REFERENCES users (id)
)

;
CREATE INDEX IF NOT EXISTS ix_merge_proofreading_runs_status ON merge_proofreading_runs (status);
CREATE INDEX IF NOT EXISTS ix_merge_proofreading_runs_view_id ON merge_proofreading_runs (view_id);

CREATE TABLE IF NOT EXISTS merge_proofreading_items (
	id UUID DEFAULT (
        lpad(to_hex(floor(random() * 4294967296)::bigint), 8, '0') || '-' ||
        lpad(to_hex(floor(random() * 65536)::int), 4, '0') || '-' ||
        '4' || substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        substr('89ab', floor(random() * 4)::int + 1, 1) ||
        substr(lpad(to_hex(floor(random() * 4096)::int), 3, '0'), 1, 3) || '-' ||
        lpad(to_hex(floor(random() * 281474976710656)::bigint), 12, '0')
    )::uuid NOT NULL, 
	run_id UUID NOT NULL, 
	file_record_id UUID NOT NULL, 
	filename VARCHAR(500) NOT NULL, 
	sort_order INTEGER NOT NULL, 
	batch_id UUID, 
	child_run_id VARCHAR(64), 
	export_task_id UUID, 
	status VARCHAR(24) NOT NULL, 
	message TEXT NOT NULL, 
	counts_json TEXT NOT NULL, 
	updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_merge_proofreading_run_file UNIQUE (run_id, file_record_id), 
	FOREIGN KEY(run_id) REFERENCES merge_proofreading_runs (id) ON DELETE CASCADE, 
	FOREIGN KEY(file_record_id) REFERENCES file_records (id) ON DELETE CASCADE, 
	FOREIGN KEY(batch_id) REFERENCES proofreading_batches (id) ON DELETE SET NULL, 
	FOREIGN KEY(export_task_id) REFERENCES file_export_tasks (id) ON DELETE SET NULL
)

;
CREATE INDEX IF NOT EXISTS ix_merge_proofreading_items_run_id ON merge_proofreading_items (run_id);
