from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from arq.jobs import serialize_job
from arq.utils import timestamp_ms
from arq.worker import create_worker
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.routers import api


IMPORT_JOBS = (
    "process_import_task_job",
    "tm_resource_import_job",
    "term_resource_import_job",
    "glossary_resource_import_job",
)


@pytest.mark.parametrize("timeout", [60, 900, 3600, 7200])
def test_import_timeout_configuration_preserves_enqueue_names(timeout):
    with patch.object(api, "get_settings", return_value=SimpleNamespace(
        import_worker_job_timeout_seconds=timeout,
    )):
        functions = api._build_import_arq_functions()
    assert tuple(function.name for function in functions) == IMPORT_JOBS
    assert all(function.timeout_s == timeout for function in functions)


@pytest.mark.parametrize("timeout", [-1, 0, 59])
def test_import_timeout_rejects_invalid_values(timeout):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, import_worker_job_timeout_seconds=timeout)


def test_local_fallback_still_loads_without_arq():
    with patch.object(api, "arq_func", None):
        assert [job.__name__ for job in api._build_import_arq_functions()] == list(IMPORT_JOBS)


@pytest.mark.parametrize("settings_class,queue_name,timeout", [
    (api.AutoTMWorkerSettings, api.ARQ_AUTO_TM_QUEUE_NAME, 300),
    (api.SegmentSyncWorkerSettings, api.ARQ_SEGMENT_SYNC_QUEUE_NAME, 300),
    (api.PretranslationWorkerSettings, api.ARQ_PRETRANSLATION_QUEUE_NAME, 300),
    (api.TranslationReviewWorkerSettings, api.ARQ_TRANSLATION_REVIEW_QUEUE_NAME, 300),
    (api.AiWorkerSettings, api.ARQ_AI_QUEUE_NAME,
     max(get_settings().ai_worker_job_timeout_seconds, 60) + 60),
])
def test_other_workers_keep_their_timeouts_and_queues(settings_class, queue_name, timeout):
    async def scenario():
        worker = create_worker(settings_class, redis_pool=MagicMock(), handle_signals=False)
        assert worker.queue_name == queue_name
        assert worker.job_timeout_s == timeout
        assert all(function.timeout_s is None for function in worker.functions.values())

    asyncio.run(scenario())


@pytest.mark.parametrize("settings_class", [api.ImportWorkerSettings, api.MaintenanceWorkerSettings])
def test_real_arq_registration_keeps_import_timeout_scoped(settings_class):
    async def scenario():
        worker = create_worker(settings_class, redis_pool=MagicMock(), handle_signals=False)
        configured = get_settings().import_worker_job_timeout_seconds
        for name in IMPORT_JOBS:
            assert worker.functions[name].timeout_s == configured
        if settings_class is api.MaintenanceWorkerSettings:
            assert worker.job_timeout_s == 300
            for name in set(worker.functions) - set(IMPORT_JOBS):
                assert worker.functions[name].timeout_s is None
        else:
            assert worker.job_timeout_s == configured
        # arq 锁的有效期必须覆盖长导入，避免尚未完成就被其他 worker 再次拾取。
        assert worker.in_progress_timeout_s >= configured + 10

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "name,should_complete",
    [("process_import_task_job", True), ("spelling_grammar_qa_project_job", False)],
)
def test_real_arq_execution_allows_long_import_but_times_out_maintenance(name, should_complete):
    async def scenario():
        now = timestamp_ms()
        pipe = MagicMock()
        pipe.__aenter__.return_value = pipe
        pipe.execute = AsyncMock(return_value=(serialize_job(name, (), {}, None, now), 1, True))
        pool = MagicMock()
        pool.pipeline.return_value = pipe
        worker = create_worker(api.MaintenanceWorkerSettings, redis_pool=pool, handle_signals=False)
        worker.finish_job = AsyncMock()
        worker.functions[name] = replace(worker.functions[name], coroutine=slow_job)
        original_wait_for = asyncio.wait_for

        async def scaled_wait_for(task, timeout):
            # 使用真实 arq 执行入口，把秒按比例压缩，避免测试等待五分钟或一小时。
            return await original_wait_for(task, timeout / 3000)

        with patch("arq.worker.asyncio.wait_for", scaled_wait_for):
            await worker.run_job("timeout-regression", now)
        assert worker.jobs_complete == int(should_complete)
        assert worker.jobs_failed == int(not should_complete)
        assert worker.jobs_retried == 0
        assert worker.finish_job.call_args.args[1] is True

    async def slow_job(ctx):
        await asyncio.sleep(0.2)

    asyncio.run(scenario())
