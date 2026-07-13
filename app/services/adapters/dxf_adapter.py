"""
DXF 适配器模块 - 解析 AutoCAD DXF 文件中的文本

基于 ezdxf 进行 DOM 级解析，提取 TEXT / MTEXT / ATTRIB / ATTDEF / DIMENSION 等
实体的可翻译文本。块定义（BLOCK）内的文本以及通过 INSERT 引用的属性也会被提取，
保证从 DWG 转换来的复杂图纸也能完整覆盖。

支持"文本语义重建"模式（enable_spatial_merge）：
- 核心思路：做"逻辑合并（text reconstruction）"而不是"几何合并"
- 按"阅读顺序"重建句子：同一 baseline + 同一方向 + 距离在阈值内
- 建立 sentence-level grouping，翻译在句子级做
- 回填时用单一 MTEXT 重新生成，清空原碎片实体文本

排版容错规则：
- gap < 1.5 × 字高 → 同句
- rotation diff < 5° → 同句
- baseline diff < tolerance → 同句
"""
from __future__ import annotations

import io
import json
import logging
import re
from typing import Iterable, List, Optional

from app.config import get_settings
from app.services.adapters.base import FormatAdapter
from app.services.adapters.exceptions import ParseError
from app.services.adapters.models import (
    BlockNode,
    DocumentAST,
    NodeType,
    ParseResult,
)
from app.services.adapters.segment_extractor import extract_segments
from app.services.adapters.text_reconstruction import (
    TextEntity,
    TextReconstructor,
    Sentence,
    estimate_text_width,
)


logger = logging.getLogger(__name__)


# 哪些实体类型携带可翻译文本
_TEXT_ENTITY_TYPES = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF", "DIMENSION", "MULTILEADER", "ACAD_TABLE"}

# MTEXT / 多行文本格式控制码清理
_MTEXT_FORMAT_PATTERNS = (
    re.compile(r"\\[Ll]"),                # 下划线开关
    re.compile(r"\\[Oo]"),                # 上划线开关
    re.compile(r"\\[Kk]"),                # 删除线开关
    re.compile(r"\\F[^;]*;"),             # 字体
    re.compile(r"\\f[^;]*;"),             # 字体（小写变体）
    re.compile(r"\\H[^;]*;"),             # 字高
    re.compile(r"\\W[^;]*;"),             # 宽度
    re.compile(r"\\Q[^;]*;"),             # 倾斜
    re.compile(r"\\T[^;]*;"),             # 字符间距
    re.compile(r"\\C\d+;"),               # 颜色
    re.compile(r"\\S[^;]*;"),             # 堆叠
    re.compile(r"\\A\d;"),                # 对齐
    re.compile(r"\\p[^;]*;"),             # 段落属性
)


def clean_mtext(text: str) -> str:
    """剥离 MTEXT 格式控制码，仅保留可见文本。

    兼容 ODA File Converter 输出形态：标准 DXF 换行码是 ``\\P``，
    ODA 写 DWG 再转回 DXF 时会转义成字面 ``\\\\P``，需要同时识别。
    同时识别 ``\\X``（列断）、``\\n``（罕见的字面换行）。
    """
    if not text:
        return ""
    # 先处理 ODA 形态（字面双/三反斜杠 + P/X），再处理标准形态，顺序不能反
    text = text.replace("\\\\\\P", "\n")
    text = text.replace("\\\\P", "\n")
    text = text.replace("\\P", "\n")
    text = text.replace("\\\\\\X", "\n")
    text = text.replace("\\\\X", "\n")
    text = text.replace("\\X", "\n")
    text = text.replace("\\n", "\n")
    for pattern in _MTEXT_FORMAT_PATTERNS:
        text = pattern.sub("", text)
    text = text.replace("{", "").replace("}", "")
    # 非断行空格 \~ 和 Unicode 转义 \U+xxxx
    text = text.replace("\\~", " ")
    text = re.sub(r"\\U\+([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), text)
    # 残留的字面双反斜杠折回单反斜杠
    text = text.replace("\\\\", "\\")
    return text


def is_translatable_text(text: str) -> bool:
    """判断 DXF 文本是否包含可翻译字符（含 CJK 范围）。"""
    if not text:
        return False
    return bool(re.search(r"[A-Za-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", text))


# 纯尺寸/坐标式表达（4-100×100, +0.80, R12.5 等），DWG 路径下应跳过避免乱译
_DIMENSION_LIKE_RE = re.compile(r"^[\s\d+\-±×xX*.,:/Φφ°RrDd∅⌀]+$")


def _is_dimension_like(text: str) -> bool:
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    return bool(_DIMENSION_LIKE_RE.match(stripped))


def _visual_length(text: str) -> float:
    """估算视觉宽度：CJK 全角按 2 计，其他按 1 计。"""
    if not text:
        return 0.0
    total = 0.0
    for ch in text:
        if (
            "\u4e00" <= ch <= "\u9fff"
            or "\u3040" <= ch <= "\u30ff"
            or "\uac00" <= ch <= "\ud7af"
            or "\uff00" <= ch <= "\uffef"
        ):
            total += 2.0
        else:
            total += 1.0
    return total


class DxfAdapter(FormatAdapter):
    """DXF 文件适配器（ezdxf 实现）"""

    def supported_extensions(self) -> List[str]:
        return [".dxf"]

    def parse(self, raw_bytes: bytes) -> ParseResult:
        return self._parse_with_options(raw_bytes, skip_non_translatable=False)

    def parse_with_options(
        self,
        raw_bytes: bytes,
        filename: str = "<unknown>",
        options: dict | None = None,
    ) -> ParseResult:
        self.validate_file_size(raw_bytes, filename)
        opts = options or {}
        return self._parse_with_options(
            raw_bytes,
            skip_non_translatable=bool(opts.get("skip_non_translatable", True)),
            filename=filename,
            extract_extra_entities=bool(opts.get("extract_extra_entities", False)),
            skip_dimension_like=bool(opts.get("skip_dimension_like", False)),
            enable_spatial_merge=bool(opts.get("enable_spatial_merge", False)),
        )

    def _parse_with_options(
        self,
        raw_bytes: bytes,
        skip_non_translatable: bool,
        filename: str = "<unknown>",
        *,
        extract_extra_entities: bool = False,
        skip_dimension_like: bool = False,
        collect_audit: bool = False,
        enable_spatial_merge: bool = False,
    ) -> ParseResult:
        if not raw_bytes:
            return ParseResult(
                ast=DocumentAST(nodes=[], source_format=".dxf"),
                segments=[],
                metadata={},
            )

        try:
            doc = self._read_doc(raw_bytes)
        except ParseError:
            raise
        except Exception as exc:  # noqa: BLE001 - ezdxf 的异常种类繁多，统一包装
            raise ParseError(filename=filename, reason=f"DXF 解析失败：{exc}") from exc

        audit: Optional[List[dict]] = [] if collect_audit else None
        
        # 根据是否启用空间合并选择不同的收集策略
        if enable_spatial_merge:
            nodes = self._collect_text_nodes_with_spatial_merge(
                doc,
                skip_non_translatable=skip_non_translatable,
                skip_dimension_like=skip_dimension_like,
                audit=audit,
            )
        else:
            nodes = self._collect_text_nodes(
                doc,
                skip_non_translatable=skip_non_translatable,
                extract_extra_entities=extract_extra_entities,
                skip_dimension_like=skip_dimension_like,
                audit=audit,
            )

        ast = DocumentAST(nodes=nodes, source_format=".dxf")
        segments = extract_segments(ast)

        metadata = {
            "text_count": len(nodes),
            "skip_non_translatable": skip_non_translatable,
            "dxf_version": getattr(doc, "dxfversion", ""),
            "spatial_merge_enabled": enable_spatial_merge,
        }
        if audit is not None:
            kept = sum(1 for r in audit if r["status"] == "kept")
            zh_total = sum(1 for r in audit if r["has_chinese"])
            zh_kept = sum(1 for r in audit if r["has_chinese"] and r["status"] == "kept")
            metadata["audit"] = audit
            metadata["audit_summary"] = {
                "entities": len(audit),
                "kept": kept,
                "skipped_non_translatable": sum(
                    1 for r in audit if r["reason"] == "non_translatable"
                ),
                "skipped_dimension_like": sum(
                    1 for r in audit if r["reason"] == "dimension_like"
                ),
                "with_chinese_total": zh_total,
                "with_chinese_kept": zh_kept,
                "with_chinese_dropped": zh_total - zh_kept,
                "insert_count": getattr(self, "_last_insert_stats", {}).get("count", 0),
                "insert_max_depth": getattr(self, "_last_insert_stats", {}).get("max_depth", 0),
            }

        return ParseResult(
            ast=ast,
            segments=segments,
            metadata=metadata,
        )

    @staticmethod
    def _read_doc(raw_bytes: bytes):
        """用 ezdxf 读取字节流，自动尝试常见编码。

        优先使用 ezdxf.recover（容错模式），它对 ODA File Converter 输出的
        DXF 处理得更稳定（普通 ezdxf.read 偶尔会把高版本文件误识别为 R12）。
        """
        import ezdxf
        from ezdxf import recover
        from ezdxf.lldxf.const import DXFError

        last_exc: Optional[Exception] = None
        for encoding in ("utf-8", "utf-8-sig", "cp1252", "gb18030", "iso-8859-1"):
            try:
                text = raw_bytes.decode(encoding)
            except UnicodeDecodeError as exc:
                last_exc = exc
                continue
            stream = io.BytesIO(text.encode("utf-8"))
            try:
                doc, _auditor = recover.read(stream)
                return doc
            except DXFError as exc:
                last_exc = exc
            except Exception as exc:  # noqa: BLE001 - recover 内部可能抛各种 IO/格式异常
                last_exc = exc
            try:
                return ezdxf.read(io.StringIO(text))
            except DXFError as exc:
                last_exc = exc
                continue
        raise ParseError(filename="<unknown>", reason=f"无法读取 DXF：{last_exc}")

    def _collect_text_nodes(
        self,
        doc,
        *,
        skip_non_translatable: bool,
        extract_extra_entities: bool = False,
        skip_dimension_like: bool = False,
        audit: Optional[List[dict]] = None,
    ) -> List[BlockNode]:
        nodes: List[BlockNode] = []
        seen_handles: set[str] = set()
        # ezdxf 的 INSERT 在 layout/block 遍历时是平铺出现的，不会自动展开
        # 引用的块定义；嵌套 INSERT 是通过"INSERT 引用 BLOCK，BLOCK 里再有 INSERT"
        # 这种结构间接出现的。这里通过 BLOCK 的引用关系动态测量真实嵌套深度。
        insert_stats = {"count": 0, "max_depth": 0}
        block_depth_cache: dict[str, int] = {}

        def block_max_depth(name: str, stack: tuple[str, ...] = ()) -> int:
            if not name or name in stack:
                return 0
            if name in block_depth_cache:
                return block_depth_cache[name]
            try:
                blk = doc.blocks.get(name)
            except Exception:  # noqa: BLE001
                blk = None
            if blk is None:
                block_depth_cache[name] = 0
                return 0
            best = 0
            try:
                for sub in blk:
                    if sub.dxftype() == "INSERT":
                        sub_name = getattr(sub.dxf, "name", "")
                        best = max(best, 1 + block_max_depth(sub_name, stack + (name,)))
            except Exception:  # noqa: BLE001
                pass
            block_depth_cache[name] = best
            return best

        def _audit_record(node: BlockNode, *, status: str, reason: str = "") -> None:
            if audit is None:
                return
            text = node.text_content or ""
            audit.append(
                {
                    "handle": node.metadata.get("handle", ""),
                    "entity_type": node.metadata.get("entity_type", ""),
                    "layer": node.metadata.get("layer", ""),
                    "scope": node.metadata.get("scope", ""),
                    "text": text,
                    "has_chinese": bool(re.search(r"[\u4e00-\u9fff]", text)),
                    "status": status,
                    "reason": reason,
                }
            )

        def visit(entities: Iterable, *, scope: str, depth: int = 0) -> None:
            for entity in entities:
                handle = getattr(entity.dxf, "handle", None)
                if handle and handle in seen_handles:
                    continue
                if handle:
                    seen_handles.add(handle)
                if entity.dxftype() == "INSERT":
                    insert_stats["count"] += 1
                    block_name = getattr(entity.dxf, "name", "")
                    total_depth = depth + 1 + block_max_depth(block_name)
                    if total_depth > insert_stats["max_depth"]:
                        insert_stats["max_depth"] = total_depth
                children = self._entity_to_nodes(
                    entity,
                    scope=scope,
                    extract_extra_entities=extract_extra_entities,
                )
                for node in children:
                    if node is None:
                        continue
                    text = node.text_content or ""
                    if skip_non_translatable and not is_translatable_text(text):
                        logger.debug(
                            "DXF 跳过(非可译) [%s|%s]: %s",
                            node.metadata.get("entity_type"),
                            node.metadata.get("layer"),
                            text[:80],
                        )
                        _audit_record(node, status="skipped", reason="non_translatable")
                        continue
                    if skip_dimension_like and _is_dimension_like(text):
                        logger.debug(
                            "DXF 跳过(尺寸式) [%s|%s]: %s",
                            node.metadata.get("entity_type"),
                            node.metadata.get("layer"),
                            text[:80],
                        )
                        _audit_record(node, status="skipped", reason="dimension_like")
                        continue
                    nodes.append(node)
                    _audit_record(node, status="kept")
                    logger.debug(
                        "DXF 抽出 [%s|%s|%s]: %s",
                        node.metadata.get("entity_type"),
                        node.metadata.get("layer"),
                        node.metadata.get("scope"),
                        text[:80],
                    )

        # 模型空间 + 各 paper space layout
        for layout in doc.layouts:
            visit(layout, scope=f"layout:{layout.name}")

        # 块定义中的文本。只排除 layout 关联块（已被 doc.layouts 覆盖），
        # 其余匿名块（*U1 / *D1 / *A1 等）必须遍历，否则会漏掉块内文本。
        # handle 级别的去重已经避免了重复入队。
        for block in doc.blocks:
            name = block.name
            if name.lower().startswith(("*model_space", "*paper_space")):
                continue
            visit(block, scope=f"block:{name}")

        logger.info(
            "DXF 抽取完成：%d 个文本节点，INSERT 总数 %d，最大嵌套深度 %d",
            len(nodes),
            insert_stats["count"],
            insert_stats["max_depth"],
        )
        if audit is not None:
            # 把 insert 统计塞回 audit 的反向通道（_parse_with_options 会读取）
            self._last_insert_stats = dict(insert_stats)
        return nodes

    def _collect_text_nodes_with_spatial_merge(
        self,
        doc,
        *,
        skip_non_translatable: bool,
        skip_dimension_like: bool = False,
        audit: Optional[List[dict]] = None,
    ) -> List[BlockNode]:
        """收集文本节点并进行语义重建
        
        核心流程：
        1. 先不急着翻译，先做"语义重建"
        2. 按"阅读顺序"重建句子：同一 baseline + 同一方向 + 距离在阈值内
        3. 建立 sentence-level grouping
        4. 合并后的 BlockNode 在 metadata 中包含 merged_handles 列表，供导出时使用
        
        排版容错规则：
        - gap < 3.0 × 字高 → 同句（允许较大间隔）
        - rotation diff < 5° → 同句
        - baseline diff < 0.8 × 字高 → 同句
        """
        seen_handles: set[str] = set()
        
        # Step 1: 收集所有文本实体信息
        all_entities: List[TextEntity] = []
        
        def collect_entity_info(entity, *, scope: str) -> None:
            handle = getattr(entity.dxf, "handle", None)
            if handle and handle in seen_handles:
                return
            if handle:
                seen_handles.add(handle)
            
            dxftype = entity.dxftype()
            
            # INSERT 递归处理 ATTRIB
            if dxftype == "INSERT":
                block_name = getattr(entity.dxf, "name", "")
                insert_scope = f"{scope}:insert:{block_name}"
                try:
                    for attrib in entity.attribs:
                        collect_entity_info(attrib, scope=insert_scope)
                except Exception:  # noqa: BLE001
                    pass
                return
            
            # 只处理 TEXT 和 MTEXT（ATTRIB/ATTDEF 在 INSERT 分支处理）
            if dxftype not in ("TEXT", "MTEXT", "ATTRIB", "ATTDEF"):
                return
            
            text_entity = self._extract_text_entity(entity, scope)
            if text_entity is None:
                return
            
            # 跳过非可译文本
            if skip_non_translatable and not is_translatable_text(text_entity.text):
                logger.debug("DXF 跳过(非可译) [%s|%s]: %s", dxftype, text_entity.layer, text_entity.text[:80])
                return
            
            # 跳过尺寸式文本
            if skip_dimension_like and _is_dimension_like(text_entity.text):
                logger.debug("DXF 跳过(尺寸式) [%s|%s]: %s", dxftype, text_entity.layer, text_entity.text[:80])
                return
            
            all_entities.append(text_entity)
        
        # 收集所有布局的实体
        for layout in doc.layouts:
            scope = f"layout:{layout.name}"
            for entity in layout:
                collect_entity_info(entity, scope=scope)
        
        # 收集块定义中的实体
        for block in doc.blocks:
            name = block.name
            if name.lower().startswith(("*model_space", "*paper_space")):
                continue
            scope = f"block:{name}"
            for entity in block:
                collect_entity_info(entity, scope=scope)
        
        # Step 2: 使用 TextReconstructor 进行语义重建
        # 支持两种合并模式：
        # 1. 同行合并：Y 接近（< 0.8 × 字高），X 有间隔
        # 2. 换行合并：Y 差 1-2 倍字高，X 范围有重叠（同一段落的多行）
        # 方案2：语义分割 - 如果句号结尾 + 大写/中文开头，不合并
        settings = get_settings()
        reconstructor = TextReconstructor(
            y_threshold_factor=0.8,       # 同行基线差异阈值
            x_gap_threshold_factor=3.0,   # X 间隔阈值
            rotation_threshold=5.0,       # 旋转角度阈值
            enable_semantic_break=settings.dwg_enable_semantic_break,  # 方案2：启用语义边界检测
        )
        
        sentences = reconstructor.reconstruct(all_entities)
        
        # 调试日志
        logger.info(
            "DXF 语义重建：收集到 %d 个实体，重建为 %d 个句子",
            len(all_entities), len(sentences)
        )
        
        # 找到包含特定文本的实体，帮助诊断
        for e in all_entities:
            if "消火栓" in e.text or "DN100" in e.text or "DN65" in e.text:
                logger.info(
                    "  [诊断] 实体 [%s|%s|%s] pos=(%.2f, %.2f) h=%.2f rot=%.1f: %s",
                    e.entity_type, e.handle, e.layer, e.x, e.y, e.height, e.rotation, repr(e.text)
                )
        
        # Step 3: 将句子转换为 BlockNode
        nodes: List[BlockNode] = []
        merged_count = 0
        
        for sentence in sentences:
            if not sentence.text.strip():
                continue
            
            is_merged = sentence.is_merged
            if is_merged:
                merged_count += 1
            
            # 构建 metadata
            handles = sentence.handles
            primary = sentence.primary_entity
            
            metadata = {
                "entity_type": "MERGED_TEXT" if is_merged else (primary.entity_type if primary else "TEXT"),
                "handle": primary.handle if primary else (handles[0] if handles else ""),
                "layer": sentence.layer,
                "scope": primary.scope if primary else "",
                "is_merged": is_merged,
                "merged_handles": handles,
                "merged_count": len(handles),
                "sentence_id": sentence.sentence_id,
            }
            
            if primary:
                metadata["primary_x"] = primary.x
                metadata["primary_y"] = primary.y
                metadata["primary_height"] = primary.height
            
            # 保存原始实体信息（供导出时精确回写）
            metadata["original_entities"] = json.dumps([
                {
                    "handle": e.handle,
                    "text": e.text,
                    "x": e.x,
                    "y": e.y,
                    "height": e.height,
                    "width": e.width,
                    "entity_type": e.entity_type,
                    "rotation": e.rotation,
                }
                for e in sentence.entities
            ], ensure_ascii=False)
            
            node = BlockNode(
                node_type=NodeType.PARAGRAPH,
                text_content=sentence.text,
                metadata=metadata,
            )
            nodes.append(node)
            
            if audit is not None:
                audit.append({
                    "handle": metadata["handle"],
                    "entity_type": metadata["entity_type"],
                    "layer": metadata["layer"],
                    "scope": metadata["scope"],
                    "text": sentence.text,
                    "has_chinese": bool(re.search(r"[\u4e00-\u9fff]", sentence.text)),
                    "status": "kept",
                    "reason": f"merged_{len(handles)}" if is_merged else "single",
                    "sentence_id": sentence.sentence_id,
                })
        
        logger.info(
            "DXF 语义重建完成：%d 个原始实体 → %d 个句子（其中 %d 个合并组）",
            len(all_entities),
            len(nodes),
            merged_count,
        )
        
        return nodes

    def _extract_text_entity(self, entity, scope: str) -> Optional[TextEntity]:
        """从 ezdxf 实体中提取 TextEntity 信息"""
        dxftype = entity.dxftype()
        if dxftype not in ("TEXT", "MTEXT", "ATTRIB", "ATTDEF"):
            return None
        
        handle = getattr(entity.dxf, "handle", "")
        layer = getattr(entity.dxf, "layer", "0")
        style = getattr(entity.dxf, "style", "") or ""
        
        # 获取文本内容和位置
        if dxftype == "TEXT":
            text = getattr(entity.dxf, "text", "") or ""
            try:
                insert = entity.dxf.insert
                x, y = float(insert[0]), float(insert[1])
            except Exception:
                x, y = 0.0, 0.0
            height = float(getattr(entity.dxf, "height", 2.5) or 2.5)
            rotation = float(getattr(entity.dxf, "rotation", 0.0) or 0.0)
            width_factor = float(getattr(entity.dxf, "width", 1.0) or 1.0)
            width = estimate_text_width(text, height, 0.6 * width_factor)
            
        elif dxftype == "MTEXT":
            raw = entity.text or ""
            text = clean_mtext(raw)
            try:
                insert = entity.dxf.insert
                x, y = float(insert[0]), float(insert[1])
            except Exception:
                x, y = 0.0, 0.0
            height = float(getattr(entity.dxf, "char_height", 2.5) or 2.5)
            rotation = float(getattr(entity.dxf, "rotation", 0.0) or 0.0)
            width = float(getattr(entity.dxf, "width", 0) or 0)
            if width <= 0:
                width = estimate_text_width(text, height)
                
        elif dxftype in ("ATTRIB", "ATTDEF"):
            text = getattr(entity.dxf, "text", "") or ""
            try:
                insert = entity.dxf.insert
                x, y = float(insert[0]), float(insert[1])
            except Exception:
                x, y = 0.0, 0.0
            height = float(getattr(entity.dxf, "height", 2.5) or 2.5)
            rotation = float(getattr(entity.dxf, "rotation", 0.0) or 0.0)
            width_factor = float(getattr(entity.dxf, "width", 1.0) or 1.0)
            width = estimate_text_width(text, height, 0.6 * width_factor)
        else:
            return None
        
        if not text.strip():
            return None
        
        return TextEntity(
            handle=handle,
            entity_type=dxftype,
            layer=layer,
            text=text.strip(),
            x=x,
            y=y,
            height=height,
            width=width,
            rotation=rotation,
            style=style,
            scope=scope,
        )

    def _entity_to_nodes(
        self,
        entity,
        *,
        scope: str,
        extract_extra_entities: bool,
    ) -> List[Optional[BlockNode]]:
        """单实体可能拆出多个 BlockNode（如 ACAD_TABLE 的多个单元格、MTEXT 的多段、INSERT 的多 ATTRIB）。"""
        dxftype = entity.dxftype()

        # INSERT 自身不带文本，但其 ATTRIB 子实体可能携带翻译内容；
        # 一个 INSERT 通常会挂多个 ATTRIB（设备号 / 名称 / 规格 / 备注），
        # 必须收集所有 ATTRIB，不能遇到第一个非空就返回。
        if dxftype == "INSERT":
            block_name = getattr(entity.dxf, "name", "")
            insert_scope = f"{scope}:insert:{block_name}"
            collected: List[Optional[BlockNode]] = []
            try:
                for attrib in entity.attribs:
                    collected.extend(
                        self._entity_to_nodes(
                            attrib,
                            scope=insert_scope,
                            extract_extra_entities=extract_extra_entities,
                        )
                    )
            except Exception as exc:  # noqa: BLE001 - 不同 ezdxf 版本接口偶有差异
                logger.debug("INSERT.attribs 遍历失败 [%s]: %s", block_name, exc)
            return collected

        # MTEXT 多段拆分仅在 DWG 路径启用（extract_extra_entities=True），
        # 避免 LLM 整段送翻时漏译尾段；.dxf 老路径保持单节点行为不变。
        if extract_extra_entities and dxftype == "MTEXT":
            mtext_nodes = self._mtext_to_nodes(entity, scope=scope)
            if mtext_nodes is not None:
                return mtext_nodes

        single = self._entity_to_node(
            entity,
            scope=scope,
            extract_extra_entities=extract_extra_entities,
        )
        if single is not None:
            return [single]

        if not extract_extra_entities:
            return []

        if dxftype == "ACAD_TABLE":
            return self._acad_table_to_nodes(entity, scope=scope)
        return []

    def _mtext_to_nodes(self, entity, *, scope: str) -> Optional[List[Optional[BlockNode]]]:
        """MTEXT 按 \\P 拆段。返回 None 表示走默认单节点路径。"""
        raw = entity.text or ""
        if not raw:
            return None
        cleaned = clean_mtext(raw)
        # 单段直接走原路径
        parts = [p.strip() for p in cleaned.split("\n")]
        non_empty = [p for p in parts if p]
        if len(non_empty) <= 1:
            return None

        nodes: List[Optional[BlockNode]] = []
        handle = getattr(entity.dxf, "handle", "")
        layer = getattr(entity.dxf, "layer", "0")
        for idx, part in enumerate(parts):
            if not part:
                continue
            nodes.append(
                BlockNode(
                    node_type=NodeType.PARAGRAPH,
                    text_content=part,
                    metadata={
                        "entity_type": "MTEXT",
                        "handle": handle,
                        "layer": layer,
                        "scope": scope,
                        "mtext_raw": raw,
                        "mtext_para_index": idx,
                        "mtext_para_total": len(parts),
                    },
                )
            )
        return nodes

    def _entity_to_node(
        self,
        entity,
        *,
        scope: str,
        extract_extra_entities: bool = False,
    ) -> Optional[BlockNode]:
        dxftype = entity.dxftype()
        if dxftype not in _TEXT_ENTITY_TYPES:
            return None

        text: Optional[str] = None
        meta: dict = {
            "entity_type": dxftype,
            "handle": getattr(entity.dxf, "handle", ""),
            "layer": getattr(entity.dxf, "layer", "0"),
            "scope": scope,
        }

        if dxftype == "TEXT":
            text = getattr(entity.dxf, "text", "") or ""
        elif dxftype == "MTEXT":
            raw = entity.text or ""
            text = clean_mtext(raw)
            meta["mtext_raw"] = raw
        elif dxftype in {"ATTRIB", "ATTDEF"}:
            text = getattr(entity.dxf, "text", "") or ""
            meta["tag"] = getattr(entity.dxf, "tag", "")
        elif dxftype == "DIMENSION":
            override = getattr(entity.dxf, "text", "") or ""
            # "<>" 表示沿用测量值，没有可翻译内容
            if override and override.strip() not in {"", "<>", " "}:
                text = override
        elif dxftype == "MULTILEADER":
            raw = self._extract_multileader_text(entity)
            if raw:
                text = clean_mtext(raw)
                meta["mtext_raw"] = raw

        if not text or not text.strip():
            return None

        return BlockNode(
            node_type=NodeType.PARAGRAPH,
            text_content=text.strip(),
            metadata=meta,
        )

    @staticmethod
    def _extract_multileader_text(entity) -> str:
        """从 MULTILEADER (MLEADER) 实体里取 MTEXT 文本，兼容多个 ezdxf 版本。"""
        # ezdxf >= 1.0：entity.context.mtext.insert / .default_content
        try:
            mtext = getattr(getattr(entity, "context", None), "mtext", None)
            if mtext is not None:
                # ezdxf 用 .default_content / .text 两种命名都出现过
                for attr in ("default_content", "text"):
                    value = getattr(mtext, attr, None)
                    if value:
                        return str(value)
        except Exception:  # noqa: BLE001
            pass
        # 旧版 / 兜底：dxf.default_content
        return getattr(entity.dxf, "default_content", "") or ""

    def _acad_table_to_nodes(self, entity, *, scope: str) -> List[Optional[BlockNode]]:
        """ACAD_TABLE：每个非空单元格各自一个 BlockNode，便于精确回写。"""
        nodes: List[Optional[BlockNode]] = []
        n_rows = getattr(entity.dxf, "n_rows", 0) or 0
        n_cols = getattr(entity.dxf, "n_cols", 0) or 0
        handle = getattr(entity.dxf, "handle", "")
        layer = getattr(entity.dxf, "layer", "0")
        for row in range(n_rows):
            for col in range(n_cols):
                try:
                    raw = entity.get_text(row, col) or ""
                except Exception:  # noqa: BLE001 - 不同 ezdxf 版本异常类型不一
                    continue
                cleaned = clean_mtext(raw).strip()
                if not cleaned:
                    continue
                nodes.append(
                    BlockNode(
                        node_type=NodeType.PARAGRAPH,
                        text_content=cleaned,
                        metadata={
                            "entity_type": "ACAD_TABLE",
                            "handle": handle,
                            "layer": layer,
                            "scope": scope,
                            "table_row": row,
                            "table_col": col,
                            "mtext_raw": raw,
                        },
                    )
                )
        return nodes
