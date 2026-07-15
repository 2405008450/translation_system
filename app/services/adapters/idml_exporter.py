"""
IDML 导出器 - 将翻译后的内容导出为 IDML 格式

保留原始文件结构，仅替换 Story XML 中的文本内容。
"""
import json
import logging
import zipfile
from collections import defaultdict
from io import BytesIO
from typing import Any, Dict, Iterable

from lxml import etree


logger = logging.getLogger(__name__)


class IdmlExporter:
    """IDML 导出器"""

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
        segments: Iterable[dict[str, Any]] | None = None,
    ) -> bytes:
        """导出翻译后的 IDML 文件
        
        Args:
            original_bytes: 原始文件字节
            translations: source_text -> target_text
            segments: 带 Story/paragraph_index 元数据的有序句段
            
        Returns:
            bytes: 翻译后的文件字节
        """
        paragraph_targets = self._build_paragraph_targets(segments or [])
        input_zip = zipfile.ZipFile(BytesIO(original_bytes), 'r')
        output_buffer = BytesIO()
        output_zip = zipfile.ZipFile(output_buffer, 'w', zipfile.ZIP_DEFLATED)
        
        for name in input_zip.namelist():
            content = input_zip.read(name)
            
            # 只处理 Stories 目录中的 XML 文件
            if name.startswith('Stories/') and name.endswith('.xml'):
                try:
                    content = self._translate_story(
                        content,
                        translations,
                        paragraph_targets.get(name, {}),
                    )
                except Exception:
                    logger.exception("IDML Story 导出失败，保留原始内容: %s", name)
            
            output_zip.writestr(name, content)
        
        input_zip.close()
        output_zip.close()
        
        return output_buffer.getvalue()

    def _translate_story(
        self,
        content: bytes,
        translations: Dict[str, str],
        paragraph_targets: Dict[int, list[dict[str, Any]]] | None = None,
    ) -> bytes:
        """翻译 Story XML 内容"""
        parser = etree.XMLParser(remove_blank_text=False, strip_cdata=False)
        root = etree.fromstring(content, parser=parser)

        # 优先按 Story 内段落坐标回写。导入阶段可能把一个 Content 拆成多个
        # 句段，也可能把多个带不同字符样式的 Content 合并成一个句段；按段落
        # 重组可以覆盖这两种边界不一致场景。
        structured_content_elements: set[Any] = set()
        paragraph_index = -1
        for para in root.iter('ParagraphStyleRange'):
            paragraph_index += 1
            paragraph_segments = (paragraph_targets or {}).get(paragraph_index)
            if paragraph_segments is None:
                continue

            content_elements = list(para.iter('Content'))
            if not content_elements:
                continue
            self._replace_paragraph_contents(content_elements, paragraph_segments)
            structured_content_elements.update(content_elements)

        # 没有结构元数据的旧调用继续使用精确文本匹配兜底。
        for content_elem in root.iter('Content'):
            if content_elem in structured_content_elements:
                continue
            if content_elem.text and content_elem.text.strip():
                text = content_elem.text.strip()
                if text in translations:
                    # 保留原始空白
                    original = content_elem.text
                    leading = original[:len(original) - len(original.lstrip())]
                    trailing = original[len(original.rstrip()):]
                    content_elem.text = leading + translations[text] + trailing
        
        return etree.tostring(root, encoding='utf-8', xml_declaration=True)

    def _build_paragraph_targets(
        self,
        segments: Iterable[dict[str, Any]],
    ) -> dict[str, dict[int, list[dict[str, Any]]]]:
        grouped: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)

        for segment in segments:
            metadata = self._load_metadata(segment.get("metadata"))
            story = str(metadata.get("story") or "").strip()
            paragraph_index = self._to_int(metadata.get("paragraph_index"))
            if not story or paragraph_index is None:
                continue
            grouped[(story, paragraph_index)].append(segment)

        result: dict[str, dict[int, list[dict[str, Any]]]] = defaultdict(dict)
        for (story, paragraph_index), paragraph_segments in grouped.items():
            replacements: list[dict[str, Any]] = []
            has_translation = False
            for segment in paragraph_segments:
                source = str(
                    segment.get("display_text")
                    or segment.get("source_text")
                    or ""
                ).strip()
                raw_target = segment.get("target_text")
                if raw_target is not None and str(raw_target) != "":
                    target = str(raw_target)
                    # 普通译文沿用既有的首尾去空格行为；纯空白译文是工作台
                    # “空格占位”的有效值，必须原样保留。
                    replacement = target if not target.strip() else target.strip()
                    translated = True
                    has_translation = True
                else:
                    replacement = source
                    translated = False
                replacements.append(
                    {
                        "source": source,
                        "replacement": replacement,
                        "translated": translated,
                    }
                )

            if has_translation:
                result[story][paragraph_index] = replacements

        return dict(result)

    @staticmethod
    def _join_target_parts(parts: Iterable[str]) -> str:
        result = ""
        for part in parts:
            if not part:
                continue
            if not result:
                result = part
            elif result[-1].isspace() or part[0].isspace():
                result += part
            else:
                result += " " + part
        return result

    @staticmethod
    def _replace_paragraph_contents(
        content_elements: list[Any],
        paragraph_segments: list[dict[str, Any]],
    ) -> None:
        original_texts = [element.text or "" for element in content_elements]
        paragraph_text = "".join(original_texts)
        run_spans: list[tuple[int, int]] = []
        offset = 0
        for text in original_texts:
            run_spans.append((offset, offset + len(text)))
            offset += len(text)

        resolved: list[dict[str, Any]] = []
        search_start = 0
        for segment in paragraph_segments:
            source = str(segment.get("source") or "")
            if not source:
                continue
            start = paragraph_text.find(source, search_start)
            if start < 0:
                IdmlExporter._replace_paragraph_as_one_run(
                    content_elements,
                    original_texts,
                    IdmlExporter._join_target_parts(
                        str(item.get("replacement") or "")
                        for item in paragraph_segments
                    ),
                )
                return
            end = start + len(source)
            resolved.append({**segment, "start": start, "end": end})
            search_start = end

        # 相邻句段在中文原文中通常没有空格；英文译文回写时补一个分隔空格。
        for index in range(len(resolved) - 1):
            current = resolved[index]
            following = resolved[index + 1]
            replacement = str(current.get("replacement") or "")
            next_replacement = str(following.get("replacement") or "")
            if (
                current["end"] == following["start"]
                and replacement
                and next_replacement
                and not replacement[-1].isspace()
                and not next_replacement[0].isspace()
            ):
                current["replacement"] = replacement + " "

        operations: dict[int, list[tuple[int, int, str]]] = defaultdict(list)
        for segment in resolved:
            if not segment.get("translated"):
                continue
            start = int(segment["start"])
            end = int(segment["end"])
            intersections: list[tuple[int, int, int, int]] = []
            for run_index, (run_start, run_end) in enumerate(run_spans):
                overlap_start = max(start, run_start)
                overlap_end = min(end, run_end)
                if overlap_start < overlap_end:
                    intersections.append(
                        (run_index, overlap_start, overlap_end, overlap_end - overlap_start)
                    )
            if not intersections:
                continue

            # 跨字符样式的句段无法可靠拆分译文，放到原文占比最大的主样式节点。
            anchor_index = max(
                intersections,
                key=lambda item: (item[3], len(original_texts[item[0]].strip())),
            )[0]
            for run_index, overlap_start, overlap_end, _ in intersections:
                run_start, _ = run_spans[run_index]
                operations[run_index].append(
                    (
                        overlap_start - run_start,
                        overlap_end - run_start,
                        str(segment.get("replacement") or "")
                        if run_index == anchor_index
                        else "",
                    )
                )

        for run_index, run_operations in operations.items():
            original = original_texts[run_index]
            pieces: list[str] = []
            cursor = 0
            for start, end, replacement in sorted(run_operations, key=lambda item: item[0]):
                if start < cursor:
                    IdmlExporter._replace_paragraph_as_one_run(
                        content_elements,
                        original_texts,
                        IdmlExporter._join_target_parts(
                            str(item.get("replacement") or "")
                            for item in paragraph_segments
                        ),
                    )
                    return
                pieces.append(original[cursor:start])
                pieces.append(replacement)
                cursor = end
            pieces.append(original[cursor:])
            content_elements[run_index].text = "".join(pieces)

    @staticmethod
    def _replace_paragraph_as_one_run(
        content_elements: list[Any],
        original_texts: list[str],
        target_text: str,
    ) -> None:
        # 选择原文字数最多的节点作为主样式，避免短标签样式覆盖整段译文。
        anchor_index = max(
            range(len(content_elements)),
            key=lambda index: len(original_texts[index].strip()),
        )
        first_original = original_texts[0]
        last_original = original_texts[-1]
        leading = first_original[:len(first_original) - len(first_original.lstrip())]
        trailing = last_original[len(last_original.rstrip()):]

        for element in content_elements:
            element.text = ""
        content_elements[0].text = leading
        content_elements[-1].text = trailing

        anchor_prefix = leading if anchor_index == 0 else ""
        anchor_suffix = trailing if anchor_index == len(content_elements) - 1 else ""
        content_elements[anchor_index].text = anchor_prefix + target_text + anchor_suffix

    @staticmethod
    def _load_metadata(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except (TypeError, json.JSONDecodeError):
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _to_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
