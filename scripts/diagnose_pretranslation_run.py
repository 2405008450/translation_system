"""诊断预翻译运行的真实参数、分组和 LLM 失败原因。

默认只读数据库，不调用 LLM，不写回句段。加上 --probe 后才会重放一小组
LLM 请求，用于比较本地和云端同一任务在 paragraph/sentence 模式下的差异。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

SessionLocal = None
FileRecord = None
PretranslationRun = None
PretranslationTask = None
LLMTranslateRequest = None
LLMTranslationFailure = None
get_settings = None
get_file_record_source_filename = None
_build_llm_translation_tasks = None
_deduplicate_llm_translation_tasks = None
_group_tasks_for_batch = None
_group_tasks_for_paragraph = None
_load_file_record_glossary_base_ids = None
_resolve_file_record_language_pair = None
_resolve_llm_guidelines = None
iter_batch_translate = None


def _load_app_imports() -> None:
    global SessionLocal
    global FileRecord
    global PretranslationRun
    global PretranslationTask
    global LLMTranslateRequest
    global LLMTranslationFailure
    global get_settings
    global get_file_record_source_filename
    global _build_llm_translation_tasks
    global _deduplicate_llm_translation_tasks
    global _group_tasks_for_batch
    global _group_tasks_for_paragraph
    global _load_file_record_glossary_base_ids
    global _resolve_file_record_language_pair
    global _resolve_llm_guidelines
    global iter_batch_translate

    from app.config import get_settings as imported_get_settings
    from app.database import SessionLocal as imported_session_local
    from app.models import (
        FileRecord as ImportedFileRecord,
        PretranslationRun as ImportedPretranslationRun,
        PretranslationTask as ImportedPretranslationTask,
    )
    from app.routers.api import (
        LLMTranslateRequest as ImportedLLMTranslateRequest,
        _build_llm_translation_tasks as imported_build_llm_translation_tasks,
        _deduplicate_llm_translation_tasks as imported_deduplicate_llm_translation_tasks,
        _load_file_record_glossary_base_ids as imported_load_file_record_glossary_base_ids,
        _resolve_file_record_language_pair as imported_resolve_file_record_language_pair,
        _resolve_llm_guidelines as imported_resolve_llm_guidelines,
    )
    from app.services.file_record_service import get_file_record_source_filename as imported_get_source_filename
    from app.services.llm_service import (
        LLMTranslationFailure as ImportedLLMTranslationFailure,
        _group_tasks_for_batch as imported_group_tasks_for_batch,
        _group_tasks_for_paragraph as imported_group_tasks_for_paragraph,
        iter_batch_translate as imported_iter_batch_translate,
    )

    SessionLocal = imported_session_local
    FileRecord = ImportedFileRecord
    PretranslationRun = ImportedPretranslationRun
    PretranslationTask = ImportedPretranslationTask
    LLMTranslateRequest = ImportedLLMTranslateRequest
    LLMTranslationFailure = ImportedLLMTranslationFailure
    get_settings = imported_get_settings
    get_file_record_source_filename = imported_get_source_filename
    _build_llm_translation_tasks = imported_build_llm_translation_tasks
    _deduplicate_llm_translation_tasks = imported_deduplicate_llm_translation_tasks
    _group_tasks_for_batch = imported_group_tasks_for_batch
    _group_tasks_for_paragraph = imported_group_tasks_for_paragraph
    _load_file_record_glossary_base_ids = imported_load_file_record_glossary_base_ids
    _resolve_file_record_language_pair = imported_resolve_file_record_language_pair
    _resolve_llm_guidelines = imported_resolve_llm_guidelines
    iter_batch_translate = imported_iter_batch_translate


def _short(value: Any, limit: int = 500) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"... <truncated {len(text) - limit} chars>"


def _load_options(run: PretranslationRun | None) -> dict[str, Any]:
    if run is None:
        return {}
    try:
        value = json.loads(run.options_json or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _parse_uuid(value: str | None) -> UUID | None:
    return UUID(value) if value else None


def _latest_run(db) -> PretranslationRun | None:
    return (
        db.query(PretranslationRun)
        .order_by(PretranslationRun.created_at.desc(), PretranslationRun.id.desc())
        .first()
    )


def _select_run_and_task(db, run_id: UUID | None, task_id: UUID | None) -> tuple[PretranslationRun | None, PretranslationTask | None]:
    if task_id:
        task = db.query(PretranslationTask).filter(PretranslationTask.id == task_id).first()
        return (task.run if task else None), task

    run = db.query(PretranslationRun).filter(PretranslationRun.id == run_id).first() if run_id else _latest_run(db)
    if run is None:
        return None, None

    task = (
        db.query(PretranslationTask)
        .filter(PretranslationTask.run_id == run.id)
        .order_by(
            PretranslationTask.error_segments.desc(),
            PretranslationTask.updated_at.desc(),
            PretranslationTask.created_at.asc(),
        )
        .first()
    )
    return run, task


def _uuid_list(values: Any) -> list[UUID]:
    result: list[UUID] = []
    for item in values or []:
        try:
            result.append(UUID(str(item)))
        except (TypeError, ValueError):
            continue
    return result


def _print_runtime(options: dict[str, Any]) -> None:
    settings = get_settings()
    print("== 运行配置 ==")
    print(f"DEEPSEEK_MODEL: {settings.deepseek_model}")
    print(f"OPENROUTER_MODEL: {settings.openrouter_model}")
    print(f"LLM_TIMEOUT_SECONDS: {settings.llm_timeout_seconds}")
    print(f"LLM_STALL_TIMEOUT_SECONDS: {settings.llm_stall_timeout_seconds}")
    print(f"LLM_MAX_CONCURRENCY: {settings.llm_max_concurrency}")
    print(f"LLM_RETRY_ATTEMPTS_PER_PROVIDER: {settings.llm_retry_attempts_per_provider}")
    print()
    print("== run.options_json 关键字段 ==")
    keys = [
        "use_tm",
        "tm_collection_ids",
        "tm_threshold",
        "use_glossary",
        "glossary_base_ids",
        "use_llm",
        "llm_scope",
        "llm_provider",
        "llm_model",
        "llm_translation_unit",
        "guideline_template_id",
    ]
    for key in keys:
        print(f"{key}: {options.get(key)!r}")
    print(f"temporary_prompt length: {len(str(options.get('temporary_prompt') or ''))}")


def _print_run(run: PretranslationRun, tasks: list[PretranslationTask]) -> None:
    print("== 预翻译运行 ==")
    print(f"run_id: {run.id}")
    print(f"status: {run.status}")
    print(f"created_at: {run.created_at}")
    print(f"updated_at: {run.updated_at}")
    print(f"message: {run.message}")
    print(f"files: total={run.total_files}, completed={run.completed_files}, failed={run.failed_files}, canceled={run.canceled_files}")
    print()
    print("== 文件任务 ==")
    for item in tasks:
        print(
            "task_id={task_id} file_record_id={file_record_id} status={status} stage={stage} "
            "provider={provider} model={model} processed={processed}/{total} updated={updated} errors={errors}".format(
                task_id=item.id,
                file_record_id=item.file_record_id,
                status=item.status,
                stage=item.stage,
                provider=item.provider,
                model=item.model,
                processed=item.processed_segments,
                total=item.total_segments,
                updated=item.updated_segments,
                errors=item.error_segments,
            )
        )
        if item.error:
            print(f"  error: {_short(item.error)}")
    print()


def _build_body(options: dict[str, Any], translation_unit: str | None = None) -> LLMTranslateRequest:
    use_glossary = bool(options.get("use_glossary"))
    return LLMTranslateRequest(
        scope=options.get("llm_scope") or "all",
        provider=options.get("llm_provider") or "openrouter",
        model=options.get("llm_model") or None,
        translation_unit=translation_unit or options.get("llm_translation_unit") or "paragraph",
        guideline_template_id=options.get("guideline_template_id") or None,
        temporary_prompt=options.get("temporary_prompt") or "",
        glossary_base_ids=_uuid_list(options.get("glossary_base_ids")) if use_glossary else None,
    )


def _describe_groups(tasks, unit: str) -> list[list]:
    if unit == "paragraph":
        groups = _group_tasks_for_paragraph(tasks)
        return [group.tasks for group in groups]
    groups = _group_tasks_for_batch([task for task in tasks if task.should_translate])
    return [group.tasks for group in groups]


def _print_task_plan(db, file_record: FileRecord, options: dict[str, Any], translation_unit: str | None) -> tuple[LLMTranslateRequest, list, list[list], str]:
    body = _build_body(options, translation_unit=translation_unit)
    source_language, target_language = _resolve_file_record_language_pair(file_record)
    glossary_base_ids = body.glossary_base_ids
    if glossary_base_ids is None:
        glossary_base_ids = _load_file_record_glossary_base_ids(file_record)

    guidelines = _resolve_llm_guidelines(db, file_record, body)
    tasks = _build_llm_translation_tasks(
        db=db,
        file_record_id=file_record.id,
        scope=body.scope,
        source_language=source_language,
        target_language=target_language,
        collection_id=file_record.collection_id,
        glossary_base_ids=glossary_base_ids,
        include_context=body.translation_unit == "paragraph",
        source_filename=get_file_record_source_filename(file_record),
    )
    deduplication = _deduplicate_llm_translation_tasks(tasks)
    groups = _describe_groups(deduplication.tasks, body.translation_unit)
    target_tasks = [task for task in deduplication.tasks if task.should_translate]

    print("== LLM 任务计划 ==")
    print(f"file_record_id: {file_record.id}")
    print(f"filename: {file_record.filename}")
    print(f"language_pair: {source_language} -> {target_language}")
    print(f"provider: {body.provider}")
    print(f"model_override: {body.model or '未指定，使用环境默认模型'}")
    print(f"translation_unit: {body.translation_unit}")
    print(f"scope: {body.scope}")
    print(f"guidelines length: {len(guidelines)}")
    print(f"glossary_base_ids: {[str(item) for item in glossary_base_ids or []]}")
    print(f"tasks: total_context={len(deduplication.tasks)}, target={len(target_tasks)}, unique={deduplication.unique_total}, deduplicated={deduplication.deduplicated_count}")
    print(f"target status counts: {dict(Counter(task.status for task in target_tasks))}")
    print(f"group count: {len(groups)}")
    for index, group in enumerate(groups[:10]):
        group_targets = [task for task in group if task.should_translate]
        chars = sum(len(task.source_text or "") for task in group)
        block_keys = {
            (task.block_type, task.block_index, task.row_index, task.cell_index)
            for task in group
        }
        first_ids = [task.sentence_id for task in group_targets[:5]]
        print(
            f"  group[{index}]: items={len(group)}, targets={len(group_targets)}, chars={chars}, "
            f"block_keys={len(block_keys)}, first_target_ids={first_ids}"
        )
    if len(groups) > 10:
        print(f"  ... 其余 {len(groups) - 10} 组省略")
    print()
    return body, deduplication.tasks, groups, guidelines


async def _probe(body: LLMTranslateRequest, group_tasks: list, guidelines: str, model_override: str | None) -> int:
    print("== LLM 重放探测 ==")
    print("注意：此步骤会真实调用 LLM，但不会写回数据库。")
    updated = 0
    failed = 0
    async for item in iter_batch_translate(
        group_tasks,
        provider=body.provider,
        translation_guidelines=guidelines,
        translation_unit=body.translation_unit,
        model_override=model_override,
    ):
        if isinstance(item, LLMTranslationFailure):
            failed += 1
            print(f"FAIL sentence_id={item.sentence_id} status={item.status} error={_short(item.error_message, 800)}")
        else:
            updated += 1
            print(
                f"OK sentence_id={item.sentence_id} provider={item.provider} model={item.model} "
                f"target={_short(item.translated_text, 160)}"
            )
    print(f"probe result: ok={updated}, failed={failed}")
    return 0 if updated > 0 and failed == 0 else 3


def main() -> int:
    parser = argparse.ArgumentParser(description="诊断预翻译运行的实际参数、分组和失败原因")
    parser.add_argument("--run-id", help="指定 pretranslation_runs.id；不指定则读取最新 run")
    parser.add_argument("--task-id", help="指定 pretranslation_tasks.id；优先于 --run-id")
    parser.add_argument("--unit", choices=["paragraph", "sentence"], help="临时覆盖重放/分组使用的 llm_translation_unit")
    parser.add_argument("--probe", action="store_true", help="真实调用 LLM 重放一个分组，但不写回数据库")
    parser.add_argument("--group-index", type=int, default=0, help="--probe 时重放第几个分组，默认 0")
    parser.add_argument("--max-probe-targets", type=int, default=15, help="--probe sentence 模式最多重放多少个目标句段")
    args = parser.parse_args()

    _load_app_imports()

    with SessionLocal() as db:
        run, selected_task = _select_run_and_task(db, _parse_uuid(args.run_id), _parse_uuid(args.task_id))
        if run is None:
            print("没有找到预翻译 run。")
            return 1
        options = _load_options(run)
        tasks = (
            db.query(PretranslationTask)
            .filter(PretranslationTask.run_id == run.id)
            .order_by(PretranslationTask.created_at.asc(), PretranslationTask.id.asc())
            .all()
        )
        _print_runtime(options)
        print()
        _print_run(run, tasks)

        task = selected_task or (tasks[0] if tasks else None)
        if task is None:
            print("该 run 没有 pretranslation_tasks。")
            return 1

        file_record = db.query(FileRecord).filter(FileRecord.id == task.file_record_id).first()
        if file_record is None:
            print(f"文件不存在：{task.file_record_id}")
            return 1

        body, deduped_tasks, groups, guidelines = _print_task_plan(db, file_record, options, args.unit)

        if not args.probe:
            print("未加 --probe：只完成只读诊断，没有调用 LLM。")
            return 0

        if not groups:
            print("没有可重放的 LLM 分组。")
            return 1
        group_index = max(0, min(args.group_index, len(groups) - 1))
        group_tasks = groups[group_index]
        if body.translation_unit == "sentence":
            targets = [task for task in group_tasks if task.should_translate][: max(args.max_probe_targets, 1)]
            selected_ids = {task.sentence_id for task in targets}
            group_tasks = [task for task in deduped_tasks if task.sentence_id in selected_ids]

        return asyncio.run(_probe(body, group_tasks, guidelines, body.model))


if __name__ == "__main__":
    raise SystemExit(main())
