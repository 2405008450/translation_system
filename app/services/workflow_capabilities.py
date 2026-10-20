"""工作流能力及阶段定义；历史 proofread 标识继续表示双文档流程。"""
from __future__ import annotations

from typing import Any

STANDALONE_PROOFREADING_TEMPLATES = frozenset({"proofread", "proofread_only"})
WORKFLOW_PROOFREADING_KIND = "workflow_segments"

ALIGNMENT_STAGE_MIGRATIONS = [
    "SELECT pg_advisory_xact_lock(hashtextextended('split-alignment-proofreading', 0))",
    """UPDATE project_workflow_steps AS step SET sort_order = 1
       FROM projects AS project WHERE step.project_id = project.id
       AND project.workflow_template_id = 'proofread' AND step.step_type = 'proofread'
       AND step.sort_order <> 1""",
    """UPDATE file_assignments AS assignment SET workflow_step_id = step.id
       FROM project_workflow_steps AS step, projects AS project
       WHERE assignment.project_id = project.id AND project.workflow_template_id = 'proofread'
       AND step.project_id = project.id AND step.step_type = 'proofread'
       AND assignment.workflow_step_id IS NULL""",
    """UPDATE segments AS segment SET workflow_step_id = step.id
       FROM file_records AS file, projects AS project, project_workflow_steps AS step
       WHERE segment.file_record_id = file.id AND file.project_id = project.id
       AND project.workflow_template_id = 'proofread' AND step.project_id = project.id
       AND step.step_type = 'proofread' AND segment.workflow_step_id IS NULL""",
    """INSERT INTO project_workflow_steps (project_id, step_key, name, step_type, sort_order)
       SELECT project.id, 'alignment', '对齐', 'alignment', 0 FROM projects AS project
       WHERE project.workflow_template_id = 'proofread' AND NOT EXISTS (
           SELECT 1 FROM project_workflow_steps AS step
           WHERE step.project_id = project.id AND step.step_type = 'alignment')""",
]


def is_standalone_proofreading(value: Any) -> bool:
    return getattr(value, "workflow_template_id", value) in STANDALONE_PROOFREADING_TEMPLATES


def supports_alignment(value: Any) -> bool:
    return getattr(value, "workflow_template_id", value) == "proofread"


def standalone_steps(template_id: str) -> list[dict[str, str]]:
    proofread = {"step_key": "proofread", "name": "校对", "step_type": "proofread"}
    if supports_alignment(template_id):
        return [{"step_key": "alignment", "name": "对齐", "step_type": "alignment"}, proofread]
    return [proofread]


def proofreading_step_id(project: Any):
    return next((step.id for step in project.workflow_steps if step.step_type == "proofread"), None)
