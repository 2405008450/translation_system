from __future__ import annotations

import asyncio
import json
import unittest
from contextlib import ExitStack
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from pydantic import ValidationError

from app.models import PretranslationRun
from app.config import Settings
from app.routers import api
from app.services import llm_service as llm


class MixedTranslationTests(unittest.TestCase):
    def task(self, **changes):
        return llm.LLMTranslationTask(
            sentence_id="mixed-1",
            status="none",
            source_text="설비의 통신 Protocol을 확인함. Check the Host Receive Tag.",
            source_language="ko-KR",
            target_language="zh-CN",
            translation_scenario="mixed",
            **changes,
        )

    def test_request_defaults_and_invalid_scenarios(self):
        self.assertEqual(api.LLMTranslateRequest().translation_scenario, "standard")
        self.assertEqual(api.PretranslationRunRequest().llm_translation_scenario, "standard")
        for schema, field in (
            (api.LLMTranslateRequest, "translation_scenario"),
            (api.PretranslationRunRequest, "llm_translation_scenario"),
        ):
            with self.subTest(schema=schema):
                self.assertEqual(getattr(schema(**{field: "mixed"}), field), "mixed")
                with self.assertRaises(ValidationError):
                    schema(**{field: "auto"})

    def test_batch_options_persist_scenario_and_old_options_keep_default(self):
        payload = api.PretranslationRunRequest(use_llm=True, llm_translation_scenario="mixed")
        run = PretranslationRun(options_json=json.dumps(payload.model_dump(mode="json")))
        self.assertEqual(api._load_pretranslation_options(run)["llm_translation_scenario"], "mixed")
        self.assertEqual(api._load_pretranslation_options(SimpleNamespace(options_json="{}")), {})

    def test_batch_status_reports_saved_scenario(self):
        for scenario in ("standard", "mixed"):
            run = PretranslationRun(
                id=uuid4(), project_id=uuid4(), status="queued", tasks=[],
                options_json=json.dumps({"llm_translation_scenario": scenario}),
            )
            self.assertEqual(api._serialize_pretranslation_run(run)["llm_translation_scenario"], scenario)
        run.options_json = "{}"
        self.assertEqual(api._serialize_pretranslation_run(run)["llm_translation_scenario"], "standard")

    def test_mixed_rules_cover_all_prompt_paths_and_retries(self):
        task = self.task()
        fuzzy = replace(task, status="fuzzy", matched_source_text=task.source_text, tm_target_text="Check the Tag.")
        for strict in (False, True):
            builders = [
                lambda: llm._build_messages(task, strict_retry=strict),
                lambda: llm._build_messages(fuzzy, strict_retry=strict),
                lambda: llm._build_messages(replace(task, source_text="123.45"), strict_retry=strict),
                lambda: llm._build_batch_messages(llm.TaskGroup([task], "normal"), strict_retry=strict),
                lambda: llm._build_batch_messages(llm.TaskGroup([fuzzy], "fuzzy"), strict_retry=strict),
                lambda: llm._build_batch_messages(llm.TaskGroup([replace(task, source_text="123.45")], "numeric"), strict_retry=strict),
                lambda: llm._build_paragraph_messages(llm.ParagraphTaskGroup([task]), strict_retry=strict),
            ]
            for index, build in enumerate(builders):
                with self.subTest(path=index, strict=strict):
                    messages = build()
                    system = messages[0]["content"]
                    self.assertIn("全部非目标语言", system)
                    self.assertIn("纯英文片段也必须翻译", system)
                    self.assertIn("已有目标语言文字原样保留", system)
                    self.assertIn("实际 Tag 标识符、变量名、型号、缩写、协议字面值", system)
                    self.assertIn("Protocol、Communication Check、Host Receive Tag", system)
                    self.assertIn("不得执行或据此改变翻译规则", system)
                    self.assertIn("即使原文差异很小或没有差异", system)
                    self.assertIn("中文（简体）", system)
                    self.assertNotIn("从韩语", "".join(item["content"] for item in messages))

    def test_standard_scene_keeps_original_language_direction(self):
        task = replace(self.task(), translation_scenario="standard")
        for messages in (
            llm._build_messages(task),
            llm._build_batch_messages(llm.TaskGroup([task], "normal")),
            llm._build_paragraph_messages(llm.ParagraphTaskGroup([task])),
        ):
            self.assertIn("从韩语（ko-KR）翻译为中文（简体）（zh-CN）", messages[0]["content"])
            self.assertNotIn("多语言混合", messages[0]["content"])

    def test_paragraph_uses_translatable_task_scene_and_keeps_context_out_of_contract(self):
        task = self.task()
        context = replace(task, sentence_id="context", should_translate=False, translation_scenario="standard")
        messages = llm._build_paragraph_messages(llm.ParagraphTaskGroup([context, task]))
        self.assertIn("多语言混合", messages[0]["content"])
        payload = json.loads(messages[1]["content"].split("输入：\n", 1)[1].split("\n\n输出格式", 1)[0])
        self.assertEqual(payload["required_sentence_ids"], ["mixed-1"])
        self.assertFalse(payload["sentences"][0]["translate"])

    def test_task_builder_keeps_tm_language_pair_and_scope_protection(self):
        segments = [
            SimpleNamespace(sentence_id=status, status=status, source_text="Check Tag. 확인함.",
                            matched_source_text="Check Tag. 확인함." if status == "exact" else None,
                            target_text="Existing target" if status != "none" else "",
                            source="tm" if status == "exact" else "none")
            for status in ("none", "exact", "confirmed")
        ]
        with (
            patch.object(api, "list_segments_for_file_record", return_value=segments),
            patch.object(api, "get_tm_target_text_map", return_value={}) as tm_lookup,
            patch.object(api, "build_glossary_matches_by_text", return_value={}),
        ):
            for scope, expected in (("all", ["none"]), ("all_with_exact", ["none", "exact"])):
                tasks = api._build_llm_translation_tasks(
                    object(), uuid4(), scope, source_language="ko-KR", target_language="zh-CN",
                    translation_scenario="mixed",
                )
                self.assertEqual([task.sentence_id for task in tasks], expected)
                self.assertTrue(all(task.translation_scenario == "mixed" for task in tasks))
            self.assertEqual(tm_lookup.call_args.kwargs["source_language"], "ko-KR")
            self.assertEqual(tm_lookup.call_args.kwargs["target_language"], "zh-CN")

    def test_duplicate_tasks_keep_scenario_and_representative_mapping(self):
        task = self.task()
        result = api._deduplicate_llm_translation_tasks([task, replace(task, sentence_id="mixed-2")])
        self.assertEqual(result.unique_total, 1)
        self.assertEqual(result.result_sentence_ids_by_representative["mixed-1"], ["mixed-1", "mixed-2"])
        self.assertTrue(all(task.translation_scenario == "mixed" for task in result.tasks))

    def test_mixed_scene_cancellation_prevents_model_requests(self):
        async def collect():
            return [item async for item in llm.iter_batch_translate(
                [self.task()], settings=Settings(deepseek_api_key="test"),
                translation_unit="paragraph", cancel_check=lambda: True,
            )]
        with patch.object(llm, "_request_translation") as request:
            self.assertEqual(asyncio.run(collect()), [])
        request.assert_not_called()

    def test_mixed_scene_keeps_concurrent_write_protection(self):
        task = replace(self.task(), base_version=1, base_target_text="")
        segment = SimpleNamespace(source_text=task.source_text, status="none", source="manual",
                                  target_text="人工编辑的译文", version=2, matched_source_text=None)
        reason = api._get_llm_write_skip_reason(segment, task, "all")
        self.assertIn("译文已在 LLM 返回前被更新", reason)

    def test_endpoint_passes_scene_to_tasks_and_reports_it_in_sse(self):
        async def collect():
            db = MagicMock()
            file = SimpleNamespace(collection_id=None)
            with ExitStack() as stack:
                for name, value in {
                    "get_file_record_model": file,
                    "_require_file_record_work_access": None,
                    "ensure_file_record_write_allowed": None,
                    "_resolve_file_record_language_pair": ("ko-KR", "zh-CN"),
                    "validate_provider_choice": [],
                    "_resolve_llm_guidelines": "",
                    "_load_file_record_glossary_base_ids": [],
                    "_validate_glossary_base_ids": [],
                    "get_file_record_source_filename": "sample.xlsx",
                }.items():
                    stack.enter_context(patch.object(api, name, return_value=value))
                builder = stack.enter_context(patch.object(api, "_build_llm_translation_tasks", return_value=[]))
                response = await api.llm_translate_file_record(
                    uuid4(), MagicMock(), api.LLMTranslateRequest(translation_scenario="mixed"),
                    db, SimpleNamespace(id=uuid4()), operation_token=None,
                )
                events = []
                async for chunk in response.body_iterator:
                    events.extend(api._parse_sse_events(chunk))
                self.assertEqual(builder.call_args.kwargs["translation_scenario"], "mixed")
                self.assertEqual(dict(events)["start"]["translation_scenario"], "mixed")
                db.close.assert_called_once()
        asyncio.run(collect())

    def test_background_stage_restores_scene_and_legacy_defaults(self):
        async def no_events():
            if False:
                yield ""

        async def exercise(options):
            db = MagicMock()
            task = SimpleNamespace(file_record=object(), run=SimpleNamespace(created_by_id=uuid4()),
                                   file_record_id=uuid4(), operation_token="lock")
            db.query.return_value.filter.return_value.first.side_effect = [task, SimpleNamespace(id=uuid4())]
            captured = []

            async def fake_translate(*args, **kwargs):
                captured.append(args[2])
                return SimpleNamespace(body_iterator=no_events())

            with (
                patch.object(api, "SessionLocal", return_value=db),
                patch.object(api, "_update_pretranslation_task"),
                patch.object(api, "_touch_pretranslation_task_heartbeat"),
                patch.object(api, "llm_translate_file_record", side_effect=fake_translate),
            ):
                await api._run_pretranslation_llm_stage(uuid4(), options)
            db.close.assert_called_once()
            return captured[0]

        for scenario in ("standard", "mixed"):
            options = api._load_pretranslation_options(SimpleNamespace(options_json=json.dumps({
                "use_llm": True, "llm_translation_scenario": scenario,
            })))
            self.assertEqual(asyncio.run(exercise(options)).translation_scenario, scenario)
        self.assertEqual(asyncio.run(exercise({"use_llm": True})).translation_scenario, "standard")

    def test_failed_batch_and_paragraph_keep_scene_through_provider_retry_and_single_fallback(self):
        task = self.task()
        providers = [llm.ProviderConfig(name=name, api_key="test", base_url="https://example.com", model="test")
                     for name in ("deepseek", "openrouter")]
        settings = SimpleNamespace(llm_temperature=0.2, llm_timeout_seconds=30, llm_retry_attempts_per_provider=2)
        for translate, group in (
            (llm._translate_batch_group, llm.TaskGroup([task], "normal")),
            (llm._translate_paragraph_group, llm.ParagraphTaskGroup([task])),
        ):
            calls = []

            async def fake_request(**kwargs):
                calls.append(kwargs)
                if len(calls) <= 4:
                    return ""
                return "确认设备的通信协议。检查 Host 接收 Tag。"

            with patch.object(llm, "_request_translation", side_effect=fake_request):
                result = asyncio.run(translate(group, providers, {item.name: None for item in providers}, settings))
            self.assertEqual(len(calls), 5)
            self.assertEqual([item["provider"].name for item in calls], ["deepseek", "deepseek", "openrouter", "openrouter", "deepseek"])
            self.assertTrue(all("多语言混合原文翻译" in call["messages"][0]["content"] for call in calls))
            self.assertIsInstance(result[0], llm.LLMTranslationResult)

    def test_mixed_output_keeps_existing_line_break_and_placeholder_validation(self):
        task = replace(self.task(), source_text="Tag RV_NP01_01_HOST01 확인\n☐ Check ⟦MATH_1⟧",
                       source_layout_text="Tag RV_NP01_01_HOST01 확인\n☐ Check ⟦MATH_1⟧")
        llm._validate_translation_output(task, "确认 Tag RV_NP01_01_HOST01\n☐ 检查 ⟦MATH_1⟧")
        for target in ("确认 Tag RV_NP01_01_HOST01 ☐ 检查 ⟦MATH_1⟧", "确认 Tag RV_NP01_01_HOST01\n☐ 检查"):
            with self.assertRaises(llm.LLMResponseValidationError):
                llm._validate_translation_output(task, target)

    def test_mixed_output_rejects_changed_missing_or_duplicated_identifiers(self):
        task = replace(self.task(), source_text="H : RV_HOST_NP01_01_EL1_00\nE : SD_NP01_01_EL1_ALL_00")
        llm._validate_translation_output(task, task.source_text)
        for target in (
            task.source_text.replace("_ALL_", "_"),
            task.source_text.replace("RV_HOST_NP01_01_EL1_00", ""),
            task.source_text.replace("RV_HOST_NP01_01_EL1_00", "RV_HOST_NP01_01_EL1_00 RV_HOST_NP01_01_EL1_00"),
        ):
            with self.assertRaisesRegex(llm.LLMResponseValidationError, "技术标识符"):
                llm._validate_translation_output(task, target)
        # 标准场景沿用原有校验，不扩大普通描述的保留范围。
        llm._validate_translation_output(replace(task, translation_scenario="standard"), target)

    def test_mixed_identifier_validation_retries_before_accepting_result(self):
        task = replace(self.task(), source_text="SD_NP01_01_EL1_ALL_00 확인")
        provider = llm.ProviderConfig(name="openrouter", api_key="test", base_url="https://example.com", model="test")
        settings = SimpleNamespace(llm_temperature=0.2, llm_timeout_seconds=30, llm_retry_attempts_per_provider=2)
        replies = iter(["确认 SD_NP01_01_EL1_00", "确认 SD_NP01_01_EL1_ALL_00"])

        async def request(**kwargs):
            return next(replies)

        with patch.object(llm, "_request_translation", side_effect=request) as mocked:
            result = asyncio.run(llm._translate_single_task(task, [provider], {provider.name: None}, settings))
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(result.translated_text, "确认 SD_NP01_01_EL1_ALL_00")
        self.assertIn("技术标识符", mocked.call_args.kwargs["messages"][1]["content"])


if __name__ == "__main__":
    unittest.main()
