from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.config import Settings
from app.routers.api import LLMTranslateRequest, _build_llm_translation_tasks
from app.services.llm_service import (
    LLMTranslationResult,
    LLMTranslationTask,
    ParagraphTaskGroup,
    iter_batch_translate,
    _parse_paragraph_response,
    _task_source_hash,
)


def _settings() -> Settings:
    return Settings(
        deepseek_api_key="deepseek-key",
        openrouter_api_key=None,
        llm_max_concurrency=1,
        llm_retry_attempts_per_provider=1,
        llm_temperature=0.2,
        llm_timeout_seconds=30.0,
    )


def _paragraph_response(*tasks: LLMTranslationTask) -> str:
    return json.dumps(
        {
            "translations": {
                task.sentence_id: {
                    "source_hash": _task_source_hash(task),
                    "target_text": f"translated {task.sentence_id}",
                }
                for task in reversed(tasks)
            }
        },
        ensure_ascii=False,
    )


def test_paragraph_translation_uses_key_hash_alignment_not_response_order():
    context = LLMTranslationTask(
        sentence_id="sent-00000",
        status="exact",
        source_text="Context sentence.",
        block_index=7,
        should_translate=False,
    )
    task_1 = LLMTranslationTask(
        sentence_id="sent-00001",
        status="none",
        source_text="第一句。",
        block_index=7,
    )
    task_2 = LLMTranslationTask(
        sentence_id="sent-00002",
        status="none",
        source_text="第二句？",
        block_index=7,
    )

    async def fake_request_translation(**kwargs):
        user_content = kwargs["messages"][1]["content"]
        assert '"sent-00000"' in user_content
        assert '"translate": false' in user_content
        assert kwargs["response_format"] == {"type": "json_object"}
        return _paragraph_response(task_1, task_2)

    async def run():
        results = []
        with patch("app.services.llm_service._request_translation", side_effect=fake_request_translation) as mocked:
            async for item in iter_batch_translate(
                [context, task_1, task_2],
                provider="deepseek",
                settings=_settings(),
                translation_unit="paragraph",
            ):
                results.append(item)
        return results, mocked.call_count

    results, call_count = asyncio.run(run())

    assert call_count == 1
    assert [item.sentence_id for item in results] == ["sent-00001", "sent-00002"]
    assert [item.translated_text for item in results] == ["translated sent-00001", "translated sent-00002"]
    assert all(isinstance(item, LLMTranslationResult) for item in results)


def test_paragraph_translation_falls_back_whole_group_on_hash_mismatch():
    task_1 = LLMTranslationTask(
        sentence_id="sent-00001",
        status="none",
        source_text="第一句。",
        block_index=3,
    )
    task_2 = LLMTranslationTask(
        sentence_id="sent-00002",
        status="none",
        source_text="第二句？",
        block_index=3,
    )
    responses = iter(
        [
            json.dumps(
                {
                    "translations": {
                        "sent-00001": {"source_hash": "wrong", "target_text": "bad"},
                        "sent-00002": {"source_hash": _task_source_hash(task_2), "target_text": "bad"},
                    }
                }
            ),
            "single one",
            "single two",
        ]
    )

    async def fake_request_translation(**kwargs):
        return next(responses)

    async def run():
        results = []
        with patch("app.services.llm_service._request_translation", side_effect=fake_request_translation) as mocked:
            async for item in iter_batch_translate(
                [task_1, task_2],
                provider="deepseek",
                settings=_settings(),
                translation_unit="paragraph",
            ):
                results.append(item)
        return results, mocked.call_count

    results, call_count = asyncio.run(run())

    assert call_count == 3
    assert [item.sentence_id for item in results] == ["sent-00001", "sent-00002"]
    assert [item.translated_text for item in results] == ["single one", "single two"]


def test_parse_paragraph_response_rejects_missing_or_extra_sentence_ids():
    task_1 = LLMTranslationTask(sentence_id="sent-00001", status="none", source_text="第一句。")
    task_2 = LLMTranslationTask(sentence_id="sent-00002", status="none", source_text="第二句？")
    group = ParagraphTaskGroup(tasks=[task_1, task_2])

    raw_text = json.dumps(
        {
            "translations": {
                "sent-00001": {
                    "source_hash": _task_source_hash(task_1),
                    "target_text": "translated one",
                }
            }
        }
    )

    with pytest.raises(Exception, match="sentence_id"):
        _parse_paragraph_response(raw_text, group)


def test_llm_task_builder_can_include_scope_out_context_segments():
    segments = [
        SimpleNamespace(
            sentence_id="sent-00000",
            status="exact",
            source_text="上下文。",
            target_text="context",
            source="tm",
            matched_source_text="上下文。",
            block_type="paragraph",
            block_index=9,
            row_index=None,
            cell_index=None,
        ),
        SimpleNamespace(
            sentence_id="sent-00001",
            status="none",
            source_text="待翻译。",
            target_text="",
            source="none",
            matched_source_text=None,
            block_type="paragraph",
            block_index=9,
            row_index=None,
            cell_index=None,
        ),
    ]

    with (
        patch("app.routers.api.list_segments_for_file_record", return_value=segments),
        patch("app.routers.api.get_tm_target_text_map", return_value={}),
    ):
        context_tasks = _build_llm_translation_tasks(
            db=object(),
            file_record_id=uuid4(),
            scope="none_only",
            source_language="zh-CN",
            target_language="en-US",
            include_context=True,
        )
        scoped_tasks = _build_llm_translation_tasks(
            db=object(),
            file_record_id=uuid4(),
            scope="none_only",
            source_language="zh-CN",
            target_language="en-US",
            include_context=False,
        )

    assert [task.sentence_id for task in context_tasks] == ["sent-00000", "sent-00001"]
    assert [task.should_translate for task in context_tasks] == [False, True]
    assert [task.block_index for task in context_tasks] == [9, 9]
    assert [task.sentence_id for task in scoped_tasks] == ["sent-00001"]


def test_llm_translate_request_defaults_to_paragraph_unit():
    request = LLMTranslateRequest()

    assert request.translation_unit == "paragraph"
