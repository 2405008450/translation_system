"""
DXF 导出器 - 在原 DXF 文档上原地替换文本，最大化保留几何/样式/图层结构。

实现策略：
- 读入原始 DXF（ezdxf DOM）
- 遍历所有 layout 与具名 block 的文本类实体，按 source -> target 映射替换
- MTEXT 的格式控制码先剥离匹配，再把译文重新写回（沿用原 dxf.char_height/style）
- 写回时优先尝试与原文件相同的 dxfversion，导出 UTF-8 文本

可选行为（仅 DWG 链路启用，默认关闭以保持 .dxf 用户行为不变）：
- enable_overflow_shrink：译文超长时按视觉宽度比例缩字宽因子 / MTEXT 字高
- handle_extra_entities：回写 MULTILEADER 的 MTEXT、ACAD_TABLE 的单元格

语义重建文本导出（Spatial Merge Export）：
核心思路：做"逻辑重建"而不是"几何合并"
- 当 translations 包含合并文本信息时，启用 MTEXT 重建模式
- 方案A（推荐）：删除原 fragmented text，用"单一 MTEXT block"重新生成
- 在主实体位置创建新的 MTEXT 实体承载完整译文
- 清空所有被合并的原始 TEXT 实体的文本内容，避免重叠
"""
from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from app.services.adapters.dxf_adapter import (
    _TEXT_ENTITY_TYPES,
    _visual_length,
    clean_mtext,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DxfExportOptions:
    """DXF 导出可选行为开关。

    所有字段默认值都对应"老行为"，新调用方按需打开即可，不影响 .dxf 普通路径。
    """

    enable_overflow_shrink: bool = False
    """译文超长时缩字宽因子 / MTEXT 字高。"""

    min_width_factor: float = 0.55
    """字宽因子最低值，过低会变形。"""

    shrink_threshold: float = 1.02
    """译文/原文视觉长度比超过该阈值才开始缩。"""

    min_char_height_ratio: float = 0.5
    """字高最低缩到原值的比例，过低会看不清。"""

    handle_extra_entities: bool = False
    """处理 MULTILEADER / ACAD_TABLE 等扩展实体。"""

    fix_shx_font_for_unicode: bool = False
    """为非 ASCII 字符创建支持 Unicode 的文本样式。"""

    unicode_font_name: str = "Arial"
    """用于替换 SHX 字体的 TrueType 字体名称。"""

    enable_spatial_merge_export: bool = False
    """启用空间合并文本的 MTEXT 重建导出模式。"""


@dataclass
class MergedTextExportInfo:
    """合并文本导出信息"""
    source_text: str  # 原始合并后的源文本
    target_text: str  # 翻译后的目标文本
    primary_handle: str  # 主实体 handle
    merged_handles: List[str]  # 所有被合并的实体 handle
    primary_x: float = 0.0
    primary_y: float = 0.0
    primary_height: float = 2.5
    layer: str = "0"


class DxfExporter:
    """DXF 导出器（ezdxf 实现）"""

    def export(
        self,
        original_bytes: bytes,
        translations: Dict[str, str],
        *,
        options: DxfExportOptions | None = None,
        audit_path: str | None = None,
        merged_text_info: List[Dict] | None = None,
    ) -> bytes:
        """导出翻译后的 DXF。
        
        Args:
            original_bytes: 原始 DXF 字节
            translations: 源文本 -> 目标文本的映射
            options: 导出选项
            audit_path: 审计日志输出路径（可选）
            merged_text_info: 空间合并文本信息列表（可选），每项包含：
                - source_text: 原始合并后的源文本
                - target_text: 翻译后的目标文本  
                - primary_handle: 主实体 handle
                - merged_handles: 所有被合并的实体 handle 列表
                - primary_x, primary_y, primary_height: 主实体位置和字高
                - layer: 图层名
        """
        if not original_bytes:
            return original_bytes
        if not translations:
            return original_bytes

        opts = options or DxfExportOptions()

        doc = self._read_doc(original_bytes)
        if doc is None:
            return original_bytes

        # 如果启用 Unicode 字体修复，创建支持 Unicode 的文本样式
        unicode_style_name: Optional[str] = None
        if opts.fix_shx_font_for_unicode:
            unicode_style_name = self._ensure_unicode_style(doc, opts.unicode_font_name)

        # 规范化键，便于宽松匹配（去首尾空白）
        normalized: Dict[str, str] = {}
        no_space_map: Dict[str, str] = {}  # 移除所有空白后的映射
        for src, tgt in translations.items():
            if src is None or tgt is None:
                continue
            normalized[src] = tgt
            stripped = src.strip()
            if stripped and stripped not in normalized:
                normalized[stripped] = tgt
            # 多空白规范化
            multi_space_normalized = re.sub(r"\s+", " ", stripped)
            if multi_space_normalized and multi_space_normalized not in normalized:
                normalized[multi_space_normalized] = tgt
            # 移除所有空白（用于兜底匹配）
            no_space = re.sub(r"\s", "", stripped)
            if no_space and no_space not in no_space_map:
                no_space_map[no_space] = tgt

        # 将无空白映射合并到 normalized（作为最后的兜底）
        for no_space_key, tgt in no_space_map.items():
            if no_space_key not in normalized:
                normalized[no_space_key] = tgt

        # 处理手动合并的文本导出
        # 策略：把译文写到第一个实体（primary_handle），清空其他被合并的实体
        merged_handles_to_clear: Set[str] = set()
        merged_primary_translations: Dict[str, Tuple[str, str]] = {}  # primary_handle -> (source_text, target_text)
        
        if merged_text_info and opts.enable_spatial_merge_export:
            logger.info("DXF 导出：处理 %d 个合并文本信息", len(merged_text_info))
            for info in merged_text_info:
                handles = info.get("merged_handles", [])
                logger.debug("DXF 导出：合并组 handles=%s", handles)
                if len(handles) > 1:
                    source_text = info.get("source_text", "")
                    target_text = info.get("target_text", "")
                    primary_handle = info.get("primary_handle", handles[0] if handles else "")
                    
                    if not target_text:
                        target_text = self._lookup(source_text, normalized)
                    
                    if target_text and primary_handle:
                        # 把译文写到主实体，同时保存原文用于溢出检测
                        merged_primary_translations[primary_handle] = (source_text, target_text)
                        
                        # 清空其他被合并的实体（不包括主实体）
                        for h in handles:
                            if h != primary_handle:
                                merged_handles_to_clear.add(h)
                        
                        logger.info(
                            "DXF 导出：合并组 primary=%s, 清空=%s, target=%s...",
                            primary_handle, [h for h in handles if h != primary_handle], target_text[:30]
                        )
                    else:
                        logger.warning(
                            "DXF 导出：合并组无译文 handles=%s, source=%s...",
                            handles[:3], source_text[:30] if source_text else "(empty)"
                        )
            
            logger.info(
                "DXF 导出：共 %d 个合并组，主实体写入=%s，清空实体=%s",
                len(merged_primary_translations), 
                list(merged_primary_translations.keys()), 
                list(merged_handles_to_clear)
            )
            
            logger.info(
                "DXF 导出：共 %d 个合并组，主实体写入=%s，清空实体=%s",
                len(merged_primary_translations), 
                list(merged_primary_translations.keys()), 
                list(merged_handles_to_clear)
            )

        # 启用 audit 时收集每条实体的命中明细；默认关闭，零开销保留老行为
        audit_records: Optional[list[dict]] = [] if audit_path else None

        seen_handles: set[str] = set()
        stats = {"total": 0, "hit": 0, "miss": 0, "merged_cleared": 0, "merged_written": 0}

        def visit(entities: Iterable) -> None:
            for entity in entities:
                handle = getattr(entity.dxf, "handle", None)
                if handle and handle in seen_handles:
                    continue
                if handle:
                    seen_handles.add(handle)
                
                # 检查是否是被合并的实体，如果是则清空文本
                if handle and handle in merged_handles_to_clear:
                    logger.info("DXF 导出：发现需要清空的实体 handle=%s", handle)
                    self._clear_entity_text(entity, stats, audit_records)
                    continue
                
                # 检查是否是主实体（需要写入合并后的译文）
                if handle and handle in merged_primary_translations:
                    logger.info("DXF 导出：发现主实体 handle=%s，写入合并译文", handle)
                    source_text, target_text = merged_primary_translations[handle]
                    self._write_merged_translation(
                        entity, 
                        source_text,
                        target_text,
                        opts,
                        stats, 
                        audit_records,
                        unicode_style_name=unicode_style_name,
                    )
                    continue
                
                self._replace_in_entity(
                    entity, normalized, opts, stats, audit_records,
                    unicode_style_name=unicode_style_name,
                )

        for layout in doc.layouts:
            visit(layout)
        for block in doc.blocks:
            name = block.name
            if name.lower().startswith(("*model_space", "*paper_space")):
                continue
            visit(block)

        logger.info(
            "DXF 回写：实体 %d，命中 %d，漏匹配 %d，合并主实体写入 %d，合并清空 %d",
            stats["total"],
            stats["hit"],
            stats["miss"],
            stats.get("merged_written", 0),
            stats.get("merged_cleared", 0),
        )

        if audit_path and audit_records is not None:
            self._dump_export_audit(audit_path, audit_records, normalized, stats)

        return self._write_doc(doc)

    @staticmethod
    def _dump_export_audit(
        path: str,
        records: list[dict],
        translations: Dict[str, str],
        stats: dict,
    ) -> None:
        """把 export 阶段的命中明细写成 JSON，便于离线分析。"""
        import json
        import re as _re

        # 哪些 translations 的 source 一次也没在 DXF 里出现过
        seen_sources = {r["source"] for r in records if r.get("source")}
        unused_translations = [
            {"source": s, "target": t}
            for s, t in translations.items()
            if s not in seen_sources and _re.search(r"[\u4e00-\u9fff]", s or "")
        ]

        # 哪些含中文文本在 DXF 里没找到译文（最关键的"漏译"列表）
        zh_missing = [
            r for r in records
            if r["status"] == "miss" and _re.search(r"[\u4e00-\u9fff]", r.get("source", ""))
        ]

        summary = {
            "entities_total": stats["total"],
            "entities_hit": stats["hit"],
            "entities_miss": stats["miss"],
            "translations_size": len(translations),
            "translations_unused": len(unused_translations),
            "zh_source_missing_translation": len(zh_missing),
        }

        try:
            with open(path, "w", encoding="utf-8") as fp:
                json.dump(
                    {
                        "summary": summary,
                        "zh_missing_translation": zh_missing,
                        "unused_translations": unused_translations,
                        "records": records,
                    },
                    fp,
                    ensure_ascii=False,
                    indent=2,
                )
            logger.info("DXF export 审计写出：%s", path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DXF export 审计写出失败 %s: %s", path, exc)

    @staticmethod
    def _read_doc(raw_bytes: bytes):
        import ezdxf
        from ezdxf import recover
        from ezdxf.lldxf.const import DXFError

        for encoding in ("utf-8", "utf-8-sig", "cp1252", "gb18030", "iso-8859-1"):
            try:
                text = raw_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
            stream = io.BytesIO(text.encode("utf-8"))
            try:
                doc, _ = recover.read(stream)
                return doc
            except DXFError:
                pass
            except Exception:  # noqa: BLE001
                pass
            try:
                return ezdxf.read(io.StringIO(text))
            except DXFError:
                continue
        return None

    @staticmethod
    def _write_doc(doc) -> bytes:
        buffer = io.StringIO()
        doc.write(buffer)
        return buffer.getvalue().encode("utf-8")

    def _clear_entity_text(
        self,
        entity,
        stats: Optional[dict] = None,
        audit: Optional[list[dict]] = None,
    ) -> None:
        """清空被合并实体的文本内容，防止与主实体译文重叠。"""
        dxftype = entity.dxftype()
        handle = getattr(entity.dxf, "handle", "")
        layer = getattr(entity.dxf, "layer", "")
        
        original_text = ""
        try:
            if dxftype == "TEXT":
                original_text = getattr(entity.dxf, "text", "") or ""
                entity.dxf.text = ""
                logger.info("清空 TEXT 实体 [handle=%s]: '%s' -> ''", handle, original_text[:50])
            elif dxftype == "MTEXT":
                original_text = entity.text or ""
                entity.text = ""
                logger.info("清空 MTEXT 实体 [handle=%s]: '%s' -> ''", handle, original_text[:50])
            elif dxftype in {"ATTRIB", "ATTDEF"}:
                original_text = getattr(entity.dxf, "text", "") or ""
                entity.dxf.text = ""
                logger.info("清空 %s 实体 [handle=%s]: '%s' -> ''", dxftype, handle, original_text[:50])
            else:
                logger.warning("无法清空实体：不支持的类型 [%s|%s]", dxftype, handle)
                return
                
            if stats is not None:
                stats["merged_cleared"] = stats.get("merged_cleared", 0) + 1
                
            if audit is not None:
                audit.append({
                    "handle": handle,
                    "entity_type": dxftype,
                    "layer": layer,
                    "source": original_text,
                    "target": "",
                    "status": "merged_cleared",
                    "reason": "part_of_merged_group",
                })
        except Exception as exc:
            logger.warning("清空实体文本失败 [%s|%s]: %s", dxftype, handle, exc)

    def _write_merged_translation(
        self,
        entity,
        source_text: str,
        target_text: str,
        opts: DxfExportOptions,
        stats: Optional[dict] = None,
        audit: Optional[list[dict]] = None,
        *,
        unicode_style_name: Optional[str] = None,
    ) -> None:
        """将合并后的译文写入主实体（第一个实体）。
        
        手动合并导出时，把合并后的译文写到第一个实体，而不是创建新的 MTEXT。
        这样可以保持原始实体的位置、样式等属性。
        同时支持溢出检测，当译文比原文长时自动缩小字体。
        """
        dxftype = entity.dxftype()
        handle = getattr(entity.dxf, "handle", "")
        layer = getattr(entity.dxf, "layer", "")
        
        original_text = ""
        try:
            if dxftype == "TEXT":
                original_text = getattr(entity.dxf, "text", "") or ""
                entity.dxf.text = target_text
                # 溢出检测和缩小
                if opts.enable_overflow_shrink:
                    self._shrink_text_entity(entity, source_text, target_text, opts)
            elif dxftype == "MTEXT":
                original_text = entity.text or ""
                entity.text = target_text
                # 溢出检测和缩小
                if opts.enable_overflow_shrink:
                    self._shrink_mtext_entity(entity, source_text, target_text, opts)
            elif dxftype in {"ATTRIB", "ATTDEF"}:
                original_text = getattr(entity.dxf, "text", "") or ""
                entity.dxf.text = target_text
                # 溢出检测和缩小
                if opts.enable_overflow_shrink:
                    self._shrink_text_entity(entity, source_text, target_text, opts)
            else:
                logger.warning("无法写入合并译文：不支持的实体类型 [%s|%s]", dxftype, handle)
                return
            
            # 如果新文本包含非 ASCII 字符且启用了字体修复，切换到 Unicode 样式
            if unicode_style_name and self._has_non_ascii(target_text):
                self._apply_unicode_style(entity, unicode_style_name)
                
            if stats is not None:
                stats["merged_written"] = stats.get("merged_written", 0) + 1
                
            if audit is not None:
                audit.append({
                    "handle": handle,
                    "entity_type": dxftype,
                    "layer": layer,
                    "source": original_text,
                    "target": target_text,
                    "status": "merged_written",
                    "reason": "primary_of_merged_group",
                })
                
            logger.debug(
                "写入合并译文 [%s|%s]: %s -> %s", 
                dxftype, handle, original_text[:30], target_text[:30]
            )
        except Exception as exc:
            logger.warning("写入合并译文失败 [%s|%s]: %s", dxftype, handle, exc)

    def _create_merged_mtext_entities(
        self,
        doc,
        merged_infos: List[MergedTextExportInfo],
        opts: DxfExportOptions,
        stats: Optional[dict] = None,
        unicode_style_name: Optional[str] = None,
    ) -> None:
        """为合并文本组创建新的 MTEXT 实体。
        
        方案A（推荐）：删除原 fragmented text，用"单一 MTEXT block"重新生成
        
        排版策略：
        1. 在主实体位置创建一个 MTEXT，承载完整的译文
        2. 使用原始字高
        3. 设置合理的 box 宽度，让 CAD 软件自动处理换行
        4. 如果译文比原文长，自动调整字高避免溢出
        """
        if not merged_infos:
            return
        
        # 获取 modelspace 用于添加新实体
        try:
            msp = doc.modelspace()
        except Exception as exc:
            logger.error("无法获取 modelspace: %s", exc)
            return
        
        for info in merged_infos:
            try:
                # 计算源文本和目标文本的视觉长度
                source_len = _visual_length(info.source_text)
                target_len = _visual_length(info.target_text)
                
                # 基础字高
                char_height = info.primary_height
                
                # 估算原始文本的宽度作为 box 宽度
                # 使用较宽的 box 避免过度换行
                estimated_box_width = source_len * char_height * 0.7
                if estimated_box_width < char_height * 5:
                    estimated_box_width = char_height * 5  # 最小宽度
                
                # 如果译文明显比原文长，调整字高
                if opts.enable_overflow_shrink and target_len > source_len * 1.1:
                    ratio = target_len / source_len
                    # 缩小字高，但不低于原来的 50%
                    shrink_factor = max(0.5, 1.0 / ratio)
                    char_height = char_height * shrink_factor
                
                # 创建 MTEXT 实体
                # attachment_point: 7 = MTEXT_BOTTOM_LEFT（左下对齐）
                # 这样文本从插入点向上、向右延伸
                mtext = msp.add_mtext(
                    info.target_text,
                    dxfattribs={
                        "insert": (info.primary_x, info.primary_y, 0),
                        "char_height": round(char_height, 4),
                        "layer": info.layer,
                        "attachment_point": 7,  # MTEXT_BOTTOM_LEFT
                        "width": round(estimated_box_width, 4),
                    }
                )
                
                # 如果需要，应用 Unicode 样式
                if unicode_style_name and self._has_non_ascii(info.target_text):
                    self._apply_unicode_style(mtext, unicode_style_name)
                
                if stats is not None:
                    stats["mtext_created"] = stats.get("mtext_created", 0) + 1
                
                logger.debug(
                    "创建合并 MTEXT [layer=%s, pos=(%.2f, %.2f), height=%.2f]: %s",
                    info.layer, info.primary_x, info.primary_y, char_height, 
                    info.target_text[:50]
                )
            except Exception as exc:
                logger.error(
                    "创建合并 MTEXT 失败 [%s]: %s", 
                    info.primary_handle, exc
                )

    def _replace_in_entity(
        self,
        entity,
        translations: Dict[str, str],
        opts: DxfExportOptions,
        stats: Optional[dict] = None,
        audit: Optional[list[dict]] = None,
        *,
        unicode_style_name: Optional[str] = None,
    ) -> None:
        dxftype = entity.dxftype()

        if dxftype == "INSERT":
            for attrib in entity.attribs:
                self._replace_in_entity(
                    attrib, translations, opts, stats, audit,
                    unicode_style_name=unicode_style_name,
                )
            return

        def _record(hit: bool, original: str, *, replaced: str = "", reason: str = "") -> None:
            if stats is not None:
                stats["total"] += 1
                if hit:
                    stats["hit"] += 1
                else:
                    stats["miss"] += 1
                    if original.strip():
                        snippet = original.strip().replace("\n", " ")[:60]
                        logger.debug("DXF 漏匹配 [%s]: %s", dxftype, snippet)
            if audit is not None:
                audit.append(
                    {
                        "handle": getattr(entity.dxf, "handle", ""),
                        "entity_type": dxftype,
                        "layer": getattr(entity.dxf, "layer", ""),
                        "source": original,
                        "target": replaced,
                        "status": "hit" if hit else "miss",
                        "reason": reason,
                    }
                )

        if dxftype == "TEXT":
            current = getattr(entity.dxf, "text", "") or ""
            if not current.strip():
                return
            new_value = self._lookup(current, translations)
            # 整段找不到时，尝试按句子拆分后逐句翻译再拼接
            if new_value is None:
                new_value = self._merge_sentence_translations(current, translations)
            if new_value is not None and new_value != current:
                entity.dxf.text = new_value
                if opts.enable_overflow_shrink:
                    self._shrink_text_entity(entity, current, new_value, opts)
                # 如果新文本包含非 ASCII 字符且启用了字体修复，切换到 Unicode 样式
                if unicode_style_name and self._has_non_ascii(new_value):
                    self._apply_unicode_style(entity, unicode_style_name)
                _record(True, current, replaced=new_value)
            else:
                _record(False, current, reason="not_in_translations" if new_value is None else "same")
            return

        if dxftype == "MTEXT":
            raw = entity.text or ""
            cleaned = clean_mtext(raw)
            if not cleaned.strip():
                return
            # 优先按整段查 translations；找不到再按段独立替换（多段 MTEXT 漏译兜底）
            new_value = self._lookup(cleaned, translations)
            if new_value is None:
                new_value = self._lookup(raw, translations)
            if new_value is None and opts.handle_extra_entities and "\n" in cleaned:
                new_value = self._merge_mtext_paragraph_translations(cleaned, translations)
            if new_value is None:
                _record(False, cleaned, reason="not_in_translations")
                return
            # MTEXT.text 的 setter 会按 char_height 重新分行，不需要手动处理控制码
            entity.text = new_value
            if opts.enable_overflow_shrink:
                self._shrink_mtext_entity(entity, cleaned, new_value, opts)
            # 如果新文本包含非 ASCII 字符且启用了字体修复，切换到 Unicode 样式
            if unicode_style_name and self._has_non_ascii(new_value):
                self._apply_unicode_style(entity, unicode_style_name)
            _record(True, cleaned, replaced=new_value)
            return

        if dxftype in {"ATTRIB", "ATTDEF"}:
            current = getattr(entity.dxf, "text", "") or ""
            if not current.strip():
                return
            new_value = self._lookup(current, translations)
            if new_value is not None and new_value != current:
                entity.dxf.text = new_value
                if opts.enable_overflow_shrink:
                    self._shrink_text_entity(entity, current, new_value, opts)
                # 如果新文本包含非 ASCII 字符且启用了字体修复，切换到 Unicode 样式
                if unicode_style_name and self._has_non_ascii(new_value):
                    self._apply_unicode_style(entity, unicode_style_name)
                _record(True, current, replaced=new_value)
            else:
                _record(False, current, reason="not_in_translations" if new_value is None else "same")
            return

        if dxftype == "DIMENSION":
            current = getattr(entity.dxf, "text", "") or ""
            if not current or current.strip() in {"", "<>"}:
                return
            new_value = self._lookup(current, translations)
            if new_value is not None and new_value != current:
                entity.dxf.text = new_value
                _record(True, current, replaced=new_value)
            else:
                _record(False, current, reason="not_in_translations" if new_value is None else "same")
            return

        if opts.handle_extra_entities:
            if dxftype == "MULTILEADER":
                self._replace_multileader(entity, translations, opts)
                return
            if dxftype == "ACAD_TABLE":
                self._replace_acad_table(entity, translations, opts)
                return

        # 其它 _TEXT_ENTITY_TYPES 中的类型在不开 handle_extra_entities 时跳过
        if dxftype not in _TEXT_ENTITY_TYPES:
            return

    # ---------------------------------------------------------------------
    # 缩放 / 溢出处理
    # ---------------------------------------------------------------------

    @staticmethod
    def _shrink_text_entity(
        entity,
        original: str,
        translated: str,
        opts: DxfExportOptions,
    ) -> None:
        """单行 TEXT/ATTRIB：先缩字宽因子，仍不够再缩字高。"""
        try:
            orig_len = _visual_length(original)
            new_len = _visual_length(translated)
            if orig_len <= 0 or new_len <= 0:
                return
            
            # 计算字符数比例（不考虑视觉宽度，纯字符数）
            char_ratio = len(translated) / max(len(original), 1)
            # 取视觉比例和字符比例中较大的
            ratio = max(new_len / orig_len, char_ratio)
            
            if ratio <= opts.shrink_threshold:
                return

            # 1) 字宽因子
            current_factor = float(getattr(entity.dxf, "width", 1.0) or 1.0)
            target_factor = max(opts.min_width_factor, current_factor / ratio)
            if target_factor < current_factor:
                entity.dxf.width = round(target_factor, 4)
                # 缩完字宽后剩下的溢出比例
                remaining_ratio = ratio * (target_factor / current_factor)
            else:
                remaining_ratio = ratio

            # 2) 仍然超长就缩字高
            if remaining_ratio > 1.02:
                current_height = float(getattr(entity.dxf, "height", 0) or 0)
                if current_height > 0:
                    height_shrink = max(
                        opts.min_char_height_ratio,
                        1.0 / remaining_ratio,
                    )
                    if height_shrink < 1.0:
                        entity.dxf.height = round(current_height * height_shrink, 4)
        except Exception as exc:  # noqa: BLE001
            logger.debug("shrink TEXT 失败: %s", exc)

    @staticmethod
    def _shrink_mtext_entity(
        entity,
        original: str,
        translated: str,
        opts: DxfExportOptions,
    ) -> None:
        """MTEXT：给定 box 宽度让 ezdxf 自动换行，必要时缩字宽因子和字高。"""
        try:
            orig_len = _visual_length(original)
            new_len = _visual_length(translated)
            if orig_len <= 0 or new_len <= 0:
                return
            
            # 计算字符数比例（不考虑视觉宽度，纯字符数）
            char_ratio = len(translated) / max(len(original), 1)
            # 取视觉比例和字符比例中较大的
            ratio = max(new_len / orig_len, char_ratio)
            
            if ratio <= opts.shrink_threshold:
                return

            char_height = float(getattr(entity.dxf, "char_height", 0) or 0)
            current_box_width = float(getattr(entity.dxf, "width", 0) or 0)

            # 没有 box 宽度时给一个估算值，按原文视觉宽度 × 0.6 × 字高
            if char_height > 0 and current_box_width <= 0:
                estimated = orig_len * char_height * 0.6
                if estimated > 0:
                    entity.dxf.width = round(estimated, 4)

            # 译文长就直接按比例缩字高（曲线更陡，溢出越多缩越多）
            if char_height > 0 and ratio > 1.05:
                shrink = max(opts.min_char_height_ratio, 1.0 / ratio)
                if shrink < 1.0:
                    entity.dxf.char_height = round(char_height * shrink, 4)
        except Exception as exc:  # noqa: BLE001
            logger.debug("shrink MTEXT 失败: %s", exc)

    # ---------------------------------------------------------------------
    # 扩展实体
    # ---------------------------------------------------------------------

    def _replace_multileader(
        self,
        entity,
        translations: Dict[str, str],
        opts: DxfExportOptions,
    ) -> None:
        """MULTILEADER (MLEADER)：替换 context.mtext 里的文本。"""
        try:
            mtext = getattr(getattr(entity, "context", None), "mtext", None)
            current_attr: Optional[str] = None
            current_value = ""
            if mtext is not None:
                for attr in ("default_content", "text"):
                    value = getattr(mtext, attr, None)
                    if value:
                        current_attr = attr
                        current_value = str(value)
                        break

            if not current_value:
                # 兜底：dxf.default_content
                current_value = getattr(entity.dxf, "default_content", "") or ""

            if not current_value:
                return

            cleaned = clean_mtext(current_value)
            new_value = self._lookup(cleaned, translations) or self._lookup(current_value, translations)
            if not new_value or new_value == current_value:
                return

            if mtext is not None and current_attr is not None:
                setattr(mtext, current_attr, new_value)
            try:
                entity.dxf.default_content = new_value
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("替换 MULTILEADER 失败: %s", exc)

    def _replace_acad_table(
        self,
        entity,
        translations: Dict[str, str],
        opts: DxfExportOptions,
    ) -> None:
        """ACAD_TABLE：逐单元格替换。ezdxf 对 ACAD_TABLE 的写支持较弱，失败就跳过。"""
        n_rows = getattr(entity.dxf, "n_rows", 0) or 0
        n_cols = getattr(entity.dxf, "n_cols", 0) or 0
        for row in range(n_rows):
            for col in range(n_cols):
                try:
                    raw = entity.get_text(row, col) or ""
                except Exception:  # noqa: BLE001
                    continue
                if not raw:
                    continue
                cleaned = clean_mtext(raw)
                new_value = self._lookup(cleaned, translations) or self._lookup(raw, translations)
                if not new_value or new_value == raw:
                    continue
                try:
                    entity.set_text(row, col, new_value)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("替换 ACAD_TABLE 单元格失败 (%d,%d): %s", row, col, exc)

    def _merge_mtext_paragraph_translations(
        self,
        cleaned: str,
        translations: Dict[str, str],
    ) -> Optional[str]:
        """多段 MTEXT 兜底：按段落各自 lookup，找到至少一段时拼回。

        没匹配上的段保留原文，避免整体丢译。返回值用 ``\\P`` 重新分段，
        交由 ezdxf 的 MTEXT.text setter 处理控制码。
        """
        parts = cleaned.split("\n")
        translated_parts: list[str] = []
        any_hit = False
        for part in parts:
            stripped = part.strip()
            if not stripped:
                translated_parts.append(part)
                continue
            replacement = self._lookup(stripped, translations)
            if replacement is None:
                translated_parts.append(part)
            else:
                translated_parts.append(replacement)
                any_hit = True
        if not any_hit:
            return None
        return "\\P".join(translated_parts)

    def _merge_sentence_translations(
        self,
        text: str,
        translations: Dict[str, str],
    ) -> Optional[str]:
        """按句子拆分后逐句翻译再拼接。

        用于 TEXT 实体整段找不到翻译时的兜底处理。
        segment_extractor 会按句号分割，导致原始长文本被拆成多个句子，
        但 DXF 实体中仍是整段，导出时需要按句子逐一查找翻译再拼接。
        """
        if not text or not text.strip():
            return None
        
        # 中文和英文句子结束标点
        sentence_endings = "。？！!?."
        
        # 按句子结束标点拆分，但保留标点
        sentences: list[str] = []
        current = ""
        for i, char in enumerate(text):
            current += char
            if char in sentence_endings:
                # 英文句号后面需要有空格才算句子结束，或者是文本结尾
                if char == ".":
                    next_idx = i + 1
                    if next_idx < len(text) and text[next_idx] not in " \t\n":
                        # 句号后面紧跟非空白字符，不分句
                        continue
                sentences.append(current)
                current = ""
        if current.strip():
            sentences.append(current)
        
        if len(sentences) <= 1:
            # 只有一个句子，无需拆分
            return None
        
        translated_parts: list[str] = []
        any_hit = False
        
        for sentence in sentences:
            stripped = sentence.strip()
            if not stripped:
                translated_parts.append(sentence)
                continue
            
            replacement = self._lookup(stripped, translations)
            if replacement is None:
                # 尝试不带结尾标点的匹配
                without_ending = stripped.rstrip(sentence_endings)
                if without_ending and without_ending != stripped:
                    replacement = self._lookup(without_ending, translations)
                    if replacement:
                        # 补回标点
                        ending = stripped[len(without_ending):]
                        replacement = replacement + ending
            
            if replacement is None:
                translated_parts.append(sentence)
            else:
                translated_parts.append(replacement)
                any_hit = True
        
        if not any_hit:
            return None
        
        return "".join(translated_parts)

    @staticmethod
    def _lookup(value: str, translations: Dict[str, str]) -> Optional[str]:
        if value is None:
            return None
        if value in translations:
            return translations[value]
        stripped = value.strip()
        if stripped and stripped in translations:
            return translations[stripped]
        # 段内多空白的兜底匹配
        normalized = re.sub(r"\s+", " ", stripped)
        if normalized and normalized in translations:
            return translations[normalized]
        # 移除所有空白后的兜底匹配（处理 ODA 转换可能产生的空白差异）
        no_space = re.sub(r"\s", "", stripped)
        if no_space and no_space in translations:
            return translations[no_space]
        return None

    @staticmethod
    def _has_non_ascii(text: str) -> bool:
        """检查文本是否包含非 ASCII 字符（如西班牙语重音字母）。"""
        if not text:
            return False
        for ch in text:
            if ord(ch) > 127:
                return True
        return False

    @staticmethod
    def _ensure_unicode_style(doc, font_name: str) -> str:
        """确保文档中存在支持 Unicode 的文本样式，返回样式名称。"""
        style_name = "_UNICODE_EXPORT"
        try:
            # 检查样式是否已存在
            if style_name in doc.styles:
                return style_name
            # 创建新的文本样式，使用 TrueType 字体
            style = doc.styles.new(style_name)
            style.dxf.font = font_name
            # 不设置 bigfont，让 CAD 软件自动处理
            style.dxf.bigfont = ""
            logger.debug("创建 Unicode 文本样式: %s, 字体: %s", style_name, font_name)
            return style_name
        except Exception as exc:
            logger.warning("创建 Unicode 文本样式失败: %s", exc)
            return ""

    @staticmethod
    def _apply_unicode_style(entity, style_name: str) -> None:
        """将实体的文本样式切换为支持 Unicode 的样式。"""
        if not style_name:
            return
        try:
            entity.dxf.style = style_name
        except Exception as exc:
            logger.debug("应用 Unicode 样式失败: %s", exc)
