-- 保留旧阶段 ID、人员分配及修订，幂等补齐对齐阶段。
BEGIN;

SELECT pg_advisory_xact_lock(hashtextextended('split-alignment-proofreading', 0));

UPDATE project_workflow_steps AS step SET sort_order = 1
       FROM projects AS project WHERE step.project_id = project.id
       AND project.workflow_template_id = 'proofread' AND step.step_type = 'proofread'
       AND step.sort_order <> 1;

UPDATE file_assignments AS assignment SET workflow_step_id = step.id
       FROM project_workflow_steps AS step, projects AS project
       WHERE assignment.project_id = project.id AND project.workflow_template_id = 'proofread'
       AND step.project_id = project.id AND step.step_type = 'proofread'
       AND assignment.workflow_step_id IS NULL;

UPDATE segments AS segment SET workflow_step_id = step.id
       FROM file_records AS file, projects AS project, project_workflow_steps AS step
       WHERE segment.file_record_id = file.id AND file.project_id = project.id
       AND project.workflow_template_id = 'proofread' AND step.project_id = project.id
       AND step.step_type = 'proofread' AND segment.workflow_step_id IS NULL;

INSERT INTO project_workflow_steps (project_id, step_key, name, step_type, sort_order)
       SELECT project.id, 'alignment', '对齐', 'alignment', 0 FROM projects AS project
       WHERE project.workflow_template_id = 'proofread' AND NOT EXISTS (
           SELECT 1 FROM project_workflow_steps AS step
           WHERE step.project_id = project.id AND step.step_type = 'alignment');

COMMIT;
