"""
IDML 导出器 - 将翻译后的内容导出为 IDML 格式

保留原始文件结构，仅替换 Story XML 中的文本内容。
"""
import json
import logging
import re
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
        changed_content_elements: set[Any] = set()
        paragraph_index = -1
        for para in root.iter():
            if self._local_name(para) != "ParagraphStyleRange":
                continue
            paragraph_index += 1
            paragraph_segments = (paragraph_targets or {}).get(paragraph_index)
            if paragraph_segments is None:
                continue

            content_elements = self._owned_content_elements(para)
            if not content_elements:
                continue

            # 新解析结果带 paragraph_part_index。按 Br 分组后分别回写，
            # 避免一个说明项的译文覆盖同段落中的其他项目符号或换行项。
            part_groups = self._owned_content_groups(para)
            segments_by_part: dict[int, list[dict[str, Any]]] = defaultdict(list)
            has_complete_part_coordinates = True
            for segment in paragraph_segments:
                part_index = segment.get("paragraph_part_index")
                if part_index is None or not 0 <= part_index < len(part_groups):
                    has_complete_part_coordinates = False
                    break
                segments_by_part[part_index].append(segment)

            if has_complete_part_coordinates and segments_by_part:
                for part_index, part_segments in segments_by_part.items():
                    part_elements = part_groups[part_index]
                    original_texts = [element.text or "" for element in part_elements]
                    self._replace_paragraph_contents(part_elements, part_segments)
                    changed_content_elements.update(
                        element
                        for element, original_text in zip(
                            part_elements,
                            original_texts,
                            strict=True,
                        )
                        if (element.text or "") != original_text
                    )
            else:
                # 兼容没有 part 坐标的历史任务。
                original_texts = [element.text or "" for element in content_elements]
                self._replace_paragraph_contents(content_elements, paragraph_segments)
                changed_content_elements.update(
                    element
                    for element, original_text in zip(
                        content_elements,
                        original_texts,
                        strict=True,
                    )
                    if (element.text or "") != original_text
                )

        # 对结构回写后仍未变化的 Content 使用文本兜底。旧任务可能手动拆分
        # 过表格标签，因而无法与重新解析出的整段文本一一对应。
        for content_elem in root.iter():
            if self._local_name(content_elem) != "Content":
                continue
            if content_elem in changed_content_elements or not content_elem.text:
                continue
            content_elem.text = self._replace_content_with_text_map(
                content_elem.text,
                translations,
            )
        
        return etree.tostring(root, encoding='utf-8', xml_declaration=True)

    @staticmethod
    def _local_name(element: Any) -> str:
        if not isinstance(getattr(element, "tag", None), str):
            return ""
        return etree.QName(element).localname

    @classmethod
    def _owned_content_elements(cls, paragraph: Any) -> list[Any]:
        """仅返回当前段落直接拥有的 Content，排除嵌套表格段落。"""
        result: list[Any] = []
        for element in paragraph.iter():
            if cls._local_name(element) != "Content":
                continue
            nearest_paragraph = next(
                (
                    ancestor
                    for ancestor in element.iterancestors()
                    if cls._local_name(ancestor) == "ParagraphStyleRange"
                ),
                None,
            )
            if nearest_paragraph is paragraph:
                result.append(element)
        return result

    @classmethod
    def _owned_content_groups(cls, paragraph: Any) -> list[list[Any]]:
        """按当前段落直接拥有的 Br，把 Content 划分为视觉文本组。"""
        groups: list[list[Any]] = []
        current: list[Any] = []

        def flush() -> None:
            if current and any((element.text or "").strip() for element in current):
                groups.append(list(current))
            current.clear()

        for element in paragraph.iter():
            if element is paragraph:
                continue
            nearest_paragraph = next(
                (
                    ancestor
                    for ancestor in element.iterancestors()
                    if cls._local_name(ancestor) == "ParagraphStyleRange"
                ),
                None,
            )
            if nearest_paragraph is not paragraph:
                continue
            element_name = cls._local_name(element)
            if element_name == "Content":
                current.append(element)
            elif element_name == "Br":
                flush()

        flush()
        return groups

    @staticmethod
    def _replace_content_with_text_map(
        original: str,
        translations: Dict[str, str],
    ) -> str:
        """按完整 Content 或明显的多空格分栏标签进行安全兜底替换。"""
        if not original.strip():
            return original

        text = original.strip()
        if text in translations:
            leading = original[:len(original) - len(original.lstrip())]
            trailing = original[len(original.rstrip()):]
            return leading + translations[text] + trailing

        # 部分图示把多个独立标题放在同一个 Content 中，并用很长的空白隔开。
        # 只在这种明确边界下做子片段替换，避免把普通正文中的短词误替换。
        pieces = re.split(r"(\s{2,})", original)
        if len(pieces) == 1:
            return original

        changed = False
        for index in range(0, len(pieces), 2):
            part = pieces[index]
            key = part.strip()
            if not key or key not in translations:
                continue
            leading = part[:len(part) - len(part.lstrip())]
            trailing = part[len(part.rstrip()):]
            pieces[index] = leading + translations[key] + trailing
            changed = True
        return "".join(pieces) if changed else original

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
                segment_metadata = self._load_metadata(segment.get("metadata"))
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
                        "paragraph_part_index": self._to_int(
                            segment_metadata.get("paragraph_part_index")
                        ),
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
