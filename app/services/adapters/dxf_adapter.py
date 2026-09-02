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
import math
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
    TextFlowGraph,
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


def _mtext_paragraph_heights(raw: str, nominal_height: float) -> List[float]:
    """返回每个 MTEXT 段落的有效字高，包含 ``\\H...;`` 格式缩放。"""
    fallback = max(float(nominal_height or 0), 1e-6)
    if not raw:
        return [fallback]
    try:
        from ezdxf.tools.text import MTextContext, MTextParser, TokenType

        context = MTextContext()
        context.cap_height = fallback
        heights = [fallback]
        paragraph_index = 0
        for token in MTextParser(raw, context):
            token_height = max(float(token.ctx.cap_height or fallback), 1e-6)
            heights[paragraph_index] = max(heights[paragraph_index], token_height)
            if token.type == TokenType.NEW_PARAGRAPH:
                paragraph_index += 1
                heights.append(token_height)
        return heights
    except Exception:  # noqa: BLE001 - 格式异常时退回标称字高
        return [fallback] * max(len(clean_mtext(raw).split("\n")), 1)


# 标准年份后缀：CAD 里"标准名 + CJJ/T + 98-2014" 常被拆成独立 TEXT，
# 单独看是纯数字-年份，既过不了"可译"也会被"尺寸式"拦下，
# 但它是标准编号的一部分，必须放行给合并逻辑再判断。
_STANDARD_YEAR_SUFFIX_RE = re.compile(r"^\d{1,4}-\d{4}[A-Za-z]?$")
# 括号年份碎片：'(2018' / '2018)' / '(2018)' / '（2018）' / 纯 '2018'。
# 属于 "GB50016-2014 (2018年版）" 这类被拆散的续写片段，必须放行。
# 纯 4 位年份限定 19xx/20xx，避免把 "1234" 这类无关数字误当年份。
_YEAR_FRAGMENT_RE = re.compile(r"^[()（）]?\s*(?:19|20)\d{2}\s*[()（）]?$")
# 单独的开/闭括号（半/全角）：CAD 常见的"(" / "（" 单字实体，合并层放行后
# 由几何位置决定要不要拼进相邻文本。
_LONE_BRACKET_RE = re.compile(r"^[()（）\[\]【】《》〈〉『』]+$")
# 管径/口径代码是 CAD 标注，不应作为可译单词送入模型。除了单个
# ``DN15``，还要识别 ``DN150 6in`` 和由 \P 分隔的纯管径列表。
_CAD_DIAMETER_CODE_RE = re.compile(
    r"^(?:DN|DE|OD|ID)\s*\d+(?:\.\d+)?(?:\s*[x×]\s*\d+(?:\.\d+)?)?$",
    re.IGNORECASE,
)
_CAD_DIAMETER_LINE_RE = re.compile(
    r"^(?:DN|DE|OD|ID)\s*\d+(?:\.\d+)?"
    r"(?:\s*[x×]\s*\d+(?:\.\d+)?)?"
    r"(?:\s*/?\s*[（(]\s*\d+(?:\.\d+)?\s*(?:in(?:ch(?:es)?)?|[\"″])\s*[）)]"
    r"|\s+\d+(?:\.\d+)?\s*(?:in(?:ch(?:es)?)?|[\"″]))?"
    r"[.,;，。；]?$",
    re.IGNORECASE,
)


def _is_cad_diameter_block(text: str) -> bool:
    """纯管径单行或多行列表不属于自然语言翻译单元。"""
    lines = [
        line.strip()
        for line in re.split(r"\r?\n+", clean_mtext(text or ""))
        if line.strip()
    ]
    return bool(lines) and all(
        _CAD_DIAMETER_LINE_RE.fullmatch(line) for line in lines
    )


def is_translatable_text(text: str) -> bool:
    """判断 DXF 文本是否包含可翻译字符（含 CJK 范围）。"""
    if not text:
        return False
    stripped = text.strip()
    if not stripped or _is_cad_diameter_block(stripped):
        return False
    if re.search(r"[A-Za-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", text):
        return True
    # 白名单：标准年份后缀 & 括号年份碎片，让它们进入合并候选，
    # 由合并层与前后文的字母/CJK 段一起拼回原句。
    if _STANDARD_YEAR_SUFFIX_RE.match(stripped):
        return True
    if _YEAR_FRAGMENT_RE.match(stripped):
        return True
    if _LONE_BRACKET_RE.match(stripped):
        return True
    return False


# 纯尺寸/坐标式表达（4-100×100, +0.80, R12.5 等），DWG 路径下应跳过避免乱译
_DIMENSION_LIKE_RE = re.compile(r"^[\s\d+\-±×xX*.,:/Φφ°RrDd∅⌀]+$")


def _is_dimension_like(text: str) -> bool:
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    if _is_cad_diameter_block(stripped):
        return True
    # 白名单：形如 "98-2014" / "155-2011" 的年份编号片段先放行
    if _STANDARD_YEAR_SUFFIX_RE.match(stripped):
        return False
    # 白名单：'2018' / '(2018' / '2018)' 这类年份碎片是标准名的续写
    if _YEAR_FRAGMENT_RE.match(stripped):
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
            # 上传未勾选时必须明确走 9d41142 的单实体解析分支，
            # 不再被全局 DWG_ENABLE_SPATIAL_MERGE 覆盖。
            enable_spatial_merge=bool(opts.get("enable_spatial_merge", False)),
            enable_llm_layout=bool(opts.get("enable_llm_layout", True)),
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
        enable_llm_layout: bool = True,
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
                enable_llm_layout=enable_llm_layout,
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
        enable_llm_layout: bool = True,
        audit: Optional[List[dict]] = None,
    ) -> List[BlockNode]:
        """收集文本节点并进行语义重建

        Stage-1 版本要点：
        - L0 几何补齐：通过 `_extract_text_entity` 拿真 bbox / 对齐锚点，
          INSERT 通过 transform 栈把子实体变换到世界坐标。
        - L4 逻辑分组：ATTRIB 打上 tag + insert_handle；DIMENSION / MULTILEADER /
          ACAD_TABLE 走独立节点通道，不参与几何合并。
        """
        seen_handles: set = set()
        # 记录被 INSERT 展开过的 BLOCK 名，避免顶层 for block in doc.blocks 再走一遍
        expanded_blocks: set[str] = set()
        # 直出节点通道：DIMENSION / MULTILEADER / ACAD_TABLE 不进合并
        standalone_nodes: List[BlockNode] = []
        # 通过前置翻译过滤、实际参与重建的文本实体
        all_entities: List[TextEntity] = []
        # 保留全部原始文本候选。表格里的编号/设备代码（如 1、X1、X2）本身
        # 不可翻译，但必须与同格正文一起重建，否则导出时旧 TEXT 会压在译文上。
        table_candidate_entities: List[TextEntity] = []
        # L2 网格线阻挡索引（按 scope 分桶的水平/垂直线段集合）
        from app.services.adapters.text_reconstruction import BarrierIndex, BarrierLine
        barrier_index = BarrierIndex()
        barrier_stats = {"lines": 0, "hlines": 0, "vlines": 0}

        def _accept_entity(text_entity: Optional[TextEntity]) -> bool:
            """前置过滤：只让可译文本或"明确白名单"碎片进入合并池。

            白名单包含标准年份后缀（98-2014）、括号年份（(2018/2018)）等
            被 CAD 拆散但语义上属于前文续写的固定形态。全量放行会带来大量
            无意义碎片，改变图连通结构，导致标题跨行错并入正文，故收敛。
            """
            if text_entity is None:
                return False
            if skip_non_translatable and not is_translatable_text(text_entity.text):
                logger.debug(
                    "DXF 跳过(非可译) [%s|%s]: %s",
                    text_entity.entity_type,
                    text_entity.layer,
                    text_entity.text[:80],
                )
                return False
            if skip_dimension_like and _is_dimension_like(text_entity.text):
                logger.debug(
                    "DXF 跳过(尺寸式) [%s|%s]: %s",
                    text_entity.entity_type,
                    text_entity.layer,
                    text_entity.text[:80],
                )
                return False
            return True

        def _collect_text_candidate(text_entity: Optional[TextEntity]) -> None:
            """统一收集文本，并为表格保留被前置过滤的结构化短代码。"""
            if text_entity is None:
                return
            table_candidate_entities.append(text_entity)
            if _accept_entity(text_entity):
                all_entities.append(text_entity)

        def _emit_standalone(entity, *, scope: str, entity_type: str) -> None:
            """DIMENSION / MULTILEADER / ACAD_TABLE：不走几何合并，直接出 BlockNode。"""
            try:
                if entity_type == "ACAD_TABLE":
                    standalone_nodes.extend(
                        n for n in self._acad_table_to_nodes(entity, scope=scope) if n is not None
                    )
                    return
                node = self._entity_to_node(
                    entity,
                    scope=scope,
                    extract_extra_entities=False,
                )
                if node is None:
                    return
                text = node.text_content or ""
                if skip_non_translatable and not is_translatable_text(text):
                    return
                if skip_dimension_like and _is_dimension_like(text):
                    return
                # 打上标记：这些节点不是合并组
                node.metadata.setdefault("is_merged", False)
                node.metadata.setdefault("merged_handles", [node.metadata.get("handle", "")])
                node.metadata.setdefault("merged_count", 1)
                standalone_nodes.append(node)
            except Exception as exc:  # noqa: BLE001 - 兼容不同 ezdxf 版本
                logger.debug("standalone 节点提取失败 [%s]: %s", entity_type, exc)

        def _collect_barrier_lines(entity, scope: str, transform) -> None:
            """把 LINE / LWPOLYLINE / POLYLINE 里近水平/近垂直的线段收进 barrier_index。"""
            dxftype = entity.dxftype()
            try:
                if dxftype == "LINE":
                    start = entity.dxf.start
                    end = entity.dxf.end
                    pts = [(float(start[0]), float(start[1])), (float(end[0]), float(end[1]))]
                elif dxftype == "LWPOLYLINE":
                    pts = [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
                    if getattr(entity, "closed", False) and len(pts) > 2:
                        pts.append(pts[0])
                elif dxftype == "POLYLINE":
                    pts = []
                    for v in entity.vertices:
                        loc = v.dxf.location
                        pts.append((float(loc[0]), float(loc[1])))
                    if getattr(entity, "is_closed", False) and len(pts) > 2:
                        pts.append(pts[0])
                else:
                    return
            except Exception:  # noqa: BLE001
                return

            if len(pts) < 2:
                return

            if transform is not None:
                transformed: List[tuple[float, float]] = []
                for x, y in pts:
                    try:
                        tx, ty, _ = transform.transform((x, y, 0.0))
                        transformed.append((float(tx), float(ty)))
                    except Exception:  # noqa: BLE001
                        transformed.append((x, y))
                pts = transformed

            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                dx = x2 - x1
                dy = y2 - y1
                length = math.hypot(dx, dy)
                if length <= 0:
                    continue
                # 水平：|dy| 相对 |dx| 很小；垂直：反之
                if abs(dy) <= 0.02 * abs(dx) and abs(dx) >= 1e-6:
                    y_mid = (y1 + y2) / 2.0
                    barrier_index.add(BarrierLine(
                        axis="h", pos=y_mid,
                        range_min=min(x1, x2), range_max=max(x1, x2),
                        scope=scope,
                    ))
                    barrier_stats["hlines"] += 1
                elif abs(dx) <= 0.02 * abs(dy) and abs(dy) >= 1e-6:
                    x_mid = (x1 + x2) / 2.0
                    barrier_index.add(BarrierLine(
                        axis="v", pos=x_mid,
                        range_min=min(y1, y2), range_max=max(y1, y2),
                        scope=scope,
                    ))
                    barrier_stats["vlines"] += 1
                barrier_stats["lines"] += 1

        def visit(
            entity,
            *,
            scope: str,
            transform=None,
            insert_handle: str = "",
            block_name: str = "",
            depth: int = 0,
        ) -> None:
            handle = getattr(entity.dxf, "handle", None)
            # 只对顶层遍历做 handle 去重；BLOCK 定义里的实体在不同 INSERT 下会重复出现，
            # 但同一 handle 只需在自己的 scope 内出现一次即可
            dedup_key = (handle, scope) if handle else None
            if dedup_key is not None:
                if dedup_key in seen_handles:  # type: ignore[operator]
                    return
                seen_handles.add(dedup_key)  # type: ignore[arg-type]

            dxftype = entity.dxftype()

            # L2：几何线段进 barrier_index，不影响其它分支
            if dxftype in ("LINE", "LWPOLYLINE", "POLYLINE"):
                _collect_barrier_lines(entity, scope, transform)
                return

            # L4：DIMENSION / MULTILEADER / ACAD_TABLE 单独成节点
            if dxftype in ("DIMENSION", "MULTILEADER", "ACAD_TABLE"):
                _emit_standalone(entity, scope=scope, entity_type=dxftype)
                return

            if dxftype == "INSERT":
                sub_block_name = getattr(entity.dxf, "name", "")
                sub_handle = getattr(entity.dxf, "handle", "") or ""
                insert_scope = f"{scope}:insert:{sub_block_name}"
                sub_transform = self._make_insert_transform(entity, parent=transform)

                # 1) INSERT 挂载的 ATTRIB（有真实 handle）
                try:
                    for attrib in entity.attribs:
                        text_entity = self._extract_text_entity(
                            attrib,
                            insert_scope,
                            transform=sub_transform,
                            insert_handle=sub_handle,
                            block_name=sub_block_name,
                        )
                        _collect_text_candidate(text_entity)
                except Exception:  # noqa: BLE001
                    pass

                # 2) 展开引用的 BLOCK 定义，把块内文本变换到世界坐标
                if depth < 10 and sub_block_name:
                    try:
                        block = doc.blocks.get(sub_block_name)
                    except Exception:  # noqa: BLE001
                        block = None
                    if block is not None:
                        expanded_blocks.add(sub_block_name)
                        for sub in block:
                            visit(
                                sub,
                                scope=insert_scope,
                                transform=sub_transform,
                                insert_handle=sub_handle,
                                block_name=sub_block_name,
                                depth=depth + 1,
                            )
                return

            # TEXT / MTEXT / 独立 ATTRIB / ATTDEF
            if dxftype in ("TEXT", "MTEXT", "ATTRIB", "ATTDEF"):
                # MTEXT 多段 (\P) 拆分：一个 MTEXT 内含 "9.1 ...\n9.2 ...\n9.3 ..." 时，
                # 若不拆开，翻译后整段行数变化会覆盖相邻表格/图形。
                # 每段各自作为独立 TextEntity 参与合并流程，y 按顺序递减一个行高。
                if dxftype == "MTEXT":
                    split_entities = self._split_mtext_paragraphs(
                        entity,
                        scope,
                        transform=transform,
                        insert_handle=insert_handle,
                        block_name=block_name,
                    )
                    if split_entities:
                        for te in split_entities:
                            _collect_text_candidate(te)
                        return

                text_entity = self._extract_text_entity(
                    entity,
                    scope,
                    transform=transform,
                    insert_handle=insert_handle,
                    block_name=block_name,
                )
                _collect_text_candidate(text_entity)
                return

        # 模型空间 + 各 paper space layout
        for layout in doc.layouts:
            layout_scope = f"layout:{layout.name}"
            for entity in layout:
                visit(entity, scope=layout_scope)

        # 未被任何 INSERT 展开过的 BLOCK 定义。已经通过 INSERT 走过 world 变换的块必须跳过，
        # 否则同一段文字会既以世界坐标出现、又以块局部坐标出现，把合并逻辑搞乱。
        for block in doc.blocks:
            name = block.name
            if name.lower().startswith(("*model_space", "*paper_space")):
                continue
            if name in expanded_blocks:
                continue
            scope = f"block:{name}"
            for entity in block:
                visit(entity, scope=scope, block_name=name)

        # 纯多级编号本身不可翻译，但若它紧邻同行右侧的可译正文，就必须补回
        # 重建池。TextFlowGraph 已有编号专用的跨图层/大间距规则；此前编号在
        # 前置过滤阶段就被丢弃，导致这些规则永远无法生效，导出后仍残留为
        # 独立 TEXT。
        accepted_ids = {id(entity) for entity in all_entities}
        accepted_text_entities = tuple(all_entities)
        recovered_numbering_fragments = 0
        for entity in table_candidate_entities:
            if id(entity) in accepted_ids:
                continue
            if not TextFlowGraph._is_hierarchical_number_only(entity.text):
                continue
            has_adjacent_text = False
            for other in accepted_text_entities:
                if other.scope != entity.scope:
                    continue
                if TextFlowGraph._is_hierarchical_number_only(other.text):
                    continue
                avg_height = max((entity.height + other.height) / 2.0, 1e-6)
                if abs(entity.y - other.y) > avg_height * 0.8:
                    continue
                gap = other.x - entity.right_edge
                if -avg_height <= gap <= avg_height * 20:
                    has_adjacent_text = True
                    break
            if not has_adjacent_text:
                continue
            all_entities.append(entity)
            accepted_ids.add(id(entity))
            recovered_numbering_fragments += 1
        if recovered_numbering_fragments:
            logger.info(
                "DXF 补回 %d 个同行多级编号片段参与语义重建",
                recovered_numbering_fragments,
            )

        # “表”与“8-3”常被 CAD 拆成两个 TEXT。连字符编号本身不可翻译，
        # 但必须在同行右侧紧邻独立表号前缀时补回，才能形成单独的“表8-3”
        # 翻译单元，而不是在导出阶段被错误追加到上方正文。
        accepted_text_entities = tuple(all_entities)
        recovered_table_references = 0
        for entity in table_candidate_entities:
            if id(entity) in accepted_ids:
                continue
            if not TextFlowGraph._is_table_reference_number_only(entity.text):
                continue
            for other in accepted_text_entities:
                if other.scope != entity.scope:
                    continue
                if not TextFlowGraph._is_table_reference_prefix_only(other.text):
                    continue
                avg_height = max((entity.height + other.height) / 2.0, 1e-6)
                if abs(entity.y - other.y) > avg_height * 0.8:
                    continue
                gap = entity.x - other.right_edge
                if -avg_height <= gap <= avg_height * 8:
                    all_entities.append(entity)
                    accepted_ids.add(id(entity))
                    recovered_table_references += 1
                    break
        if recovered_table_references:
            logger.info(
                "DXF 补回 %d 个同行连字符表号片段参与语义重建",
                recovered_table_references,
            )

        # 单行管径和紧邻闭合标点虽然不需要单独翻译，但它们是说明文字的
        # 行内组成部分，必须进入重建句段并在导出时随组清空。独立多行 DN
        # 列表仍保持结构化参数块，避免再次与标题/字段串成自然语言句子。
        inline_punctuation_re = re.compile(r"^[()（）\[\]【】,，。.;；:：]+$")
        accepted_text_entities = list(all_entities)
        recovered_inline_fragments = 0
        for entity in table_candidate_entities:
            if id(entity) in accepted_ids:
                continue
            cleaned = clean_mtext(entity.text or "").strip()
            is_single_diameter = bool(
                cleaned
                and "\n" not in cleaned
                and "\r" not in cleaned
                and _is_cad_diameter_block(cleaned)
            )
            is_closing_punctuation = bool(
                len(cleaned) <= 6
                and inline_punctuation_re.fullmatch(cleaned)
                and any(char in cleaned for char in ")）]】")
            )
            if not (is_single_diameter or is_closing_punctuation):
                continue

            for other in accepted_text_entities:
                if other.scope != entity.scope or other.layer != entity.layer:
                    continue
                average_height = max((entity.height + other.height) / 2.0, 1e-6)
                if abs(entity.y - other.y) > average_height * 0.8:
                    continue
                left, right = (
                    (entity, other) if entity.x <= other.x else (other, entity)
                )
                horizontal_gap = right.x - left.right_edge
                if -average_height * 2 <= horizontal_gap <= average_height * 20:
                    all_entities.append(entity)
                    accepted_text_entities.append(entity)
                    accepted_ids.add(id(entity))
                    recovered_inline_fragments += 1
                    break
        if recovered_inline_fragments:
            logger.info(
                "DXF 补回 %d 个同行管径/闭合标点片段参与语义重建",
                recovered_inline_fragments,
            )
        
        # Step 2: 使用 TextReconstructor 进行语义重建
        settings = get_settings()
        merge_enabled = getattr(settings, "dwg_enable_spatial_merge", False)
        semantic_break = getattr(settings, "dwg_enable_semantic_break", True)
        logical_grouping = getattr(settings, "dwg_enable_logical_grouping", True)
        h_tol = getattr(settings, "dwg_height_ratio_tolerance", 0.30)
        next_line_factor = getattr(settings, "dwg_next_line_gap_factor", 3.0)
        greedy = getattr(settings, "dwg_enable_greedy_merge", True)
        min_edge_score = getattr(settings, "dwg_min_edge_score", 0.15)
        iou_thr = getattr(settings, "dwg_iou_split_threshold", 0.5)
        # 收尾 L2 索引
        barrier_index.finalize()
        logger.info(
            "DXF spatial-merge 参数：merge=%s semantic_break=%s logical_grouping=%s "
            "h_tol=%.2f next_line=%.1f greedy=%s min_edge=%.2f iou_split=%.2f "
            "barriers(h=%d, v=%d)",
            merge_enabled, semantic_break, logical_grouping, h_tol, next_line_factor,
            greedy, min_edge_score, iou_thr,
            barrier_stats["hlines"], barrier_stats["vlines"],
        )
        reconstructor = TextReconstructor(
            y_threshold_factor=0.8,
            x_gap_threshold_factor=3.0,
            rotation_threshold=5.0,
            enable_semantic_break=semantic_break,
            enable_logical_grouping=logical_grouping,
            height_ratio_tolerance=h_tol,
            next_line_gap_factor=next_line_factor,
            barrier_index=barrier_index,
            enable_greedy_merge=greedy,
            min_edge_score=min_edge_score,
            iou_split_threshold=iou_thr,
        )

        # 闭合线框内的文本按单元格归组。普通语义重建会把编号条目视为独立句，
        # 但 CAD 表格导出需要一个 MTEXT 承载整格内容，才能按格宽自动折行。
        def _grid_cell_for(
            text_entity: TextEntity,
        ) -> Optional[tuple[float, float, float, float]]:
            cell = barrier_index.enclosing_cell(
                text_entity.scope,
                text_entity.x,
                text_entity.y,
                text_entity.height,
            )
            if cell is None:
                return None
            left, right, bottom, top = cell
            # 排除整张图框、房间轮廓等大闭合区域，只把接近表格行高的线框当候选。
            char_height = max(text_entity.height, 1e-6)
            if right - left > char_height * 300 or top - bottom > char_height * 50:
                return None
            # 闭合矩形也可能是带引线的说明框。只有边界与相邻行/列连续、
            # 确实形成多格网格时，才按表格单元格归组并在导出时居中。
            if not barrier_index.is_grid_cell(
                text_entity.scope,
                cell,
                tolerance=char_height * 0.1,
            ):
                return None
            return cell

        # 先对未过滤的候选识别单元格，再把与可译正文处于同一格的编号、设备
        # 代码和符号补回。它们不单独进入翻译，但会作为同一句段的一部分被原样保留。
        candidate_cells = {
            id(entity): cell
            for entity in table_candidate_entities
            if (cell := _grid_cell_for(entity)) is not None
        }
        candidates_by_cell: dict[tuple, List[TextEntity]] = {}
        for entity in table_candidate_entities:
            cell = candidate_cells.get(id(entity))
            if cell is not None:
                candidates_by_cell.setdefault((entity.scope, *cell), []).append(entity)

        def _is_force_merge_cell(entities: List[TextEntity]) -> bool:
            """仅合并单一说明段；标签、代码和值组成的标注框保持独立。"""
            short_colon_labels = []
            diameter_blocks: List[TextEntity] = []
            natural_language_entities: List[TextEntity] = []
            for entity in entities:
                text = (entity.text or "").strip()
                if _is_cad_diameter_block(text):
                    diameter_blocks.append(entity)
                    continue
                if is_translatable_text(text):
                    natural_language_entities.append(entity)
                if len(text) <= 32 and text.endswith((":", "：")):
                    short_colon_labels.append(text)

            multiline_diameter_blocks = [
                entity for entity in diameter_blocks
                if "\n" in clean_mtext(entity.text or "")
            ]
            # “字段名 + 多行 DN150/DN65...”是多个视觉对象，不是一个翻译句段。
            # 单行 DN 代码仍可作为同行标签的一部分参与普通重建。
            if multiline_diameter_blocks and natural_language_entities:
                return False
            if multiline_diameter_blocks:
                return False
            if len(short_colon_labels) >= 2:
                return False
            # 某些 CAD 把“平面：系统：”预先放在一个短实体中；它同样是
            # 图例标注而不是一段说明文字，不能把同格 DN15 等代码吸进来。
            if any(label.count(":") + label.count("：") >= 2 for label in short_colon_labels):
                return False
            return True

        force_merge_cell_keys = {
            key for key, entities in candidates_by_cell.items()
            if _is_force_merge_cell(entities)
        }
        accepted_ids = {id(entity) for entity in all_entities}
        accepted_cell_keys = {
            (entity.scope, *candidate_cells[id(entity)])
            for entity in all_entities
            if id(entity) in candidate_cells
            and (entity.scope, *candidate_cells[id(entity)]) in force_merge_cell_keys
        }
        recovered_table_fragments = 0
        for entity in table_candidate_entities:
            cell = candidate_cells.get(id(entity))
            if cell is None or id(entity) in accepted_ids:
                continue
            if (entity.scope, *cell) not in accepted_cell_keys:
                continue
            all_entities.append(entity)
            accepted_ids.add(id(entity))
            recovered_table_fragments += 1
        if recovered_table_fragments:
            logger.info(
                "DXF 表格补回 %d 个非译结构片段（编号/设备代码/符号）",
                recovered_table_fragments,
            )

        cell_by_entity_id: dict[int, tuple[float, float, float, float]] = {}
        cell_groups: dict[tuple, List[TextEntity]] = {}
        non_cell_entities: List[TextEntity] = []
        for text_entity in all_entities:
            cell = candidate_cells.get(id(text_entity))
            cell_key = (text_entity.scope, *cell) if cell is not None else None
            if cell is None or cell_key not in force_merge_cell_keys:
                non_cell_entities.append(text_entity)
                continue
            cell_by_entity_id[id(text_entity)] = cell
            cell_groups.setdefault(cell_key, []).append(text_entity)

        # 诊断：按 (scope, layer) 分桶后每桶大小分布——桶太碎（大量 1 元素桶）
        # 就说明 scope/layer 天然把同一段拆开了，几何阈值再宽也合不了。
        from collections import Counter
        bucket_sizes = Counter()
        for e in all_entities:
            bucket_sizes[(e.scope, e.layer)] += 1
        singletons = sum(1 for _, n in bucket_sizes.items() if n == 1)
        top_buckets = bucket_sizes.most_common(5)
        logger.info(
            "DXF 分桶分布：共 %d 桶，其中单元素桶 %d 个；Top5=%s",
            len(bucket_sizes), singletons,
            [(f"{s[:30]}|{l}", n) for (s, l), n in top_buckets],
        )

        sentences = reconstructor.reconstruct(
            non_cell_entities,
            enable_llm_layout=enable_llm_layout,
        )
        for group_entities in cell_groups.values():
            handles = [entity.handle for entity in group_entities]
            nodes_by_handle = {entity.handle: entity for entity in group_entities}
            sentence = reconstructor._path_to_sentence(handles, nodes_by_handle, 1.0)
            if sentence is None:
                continue

            # 单元格内按视觉行保留换行；同一行的碎片仍按原间距规则拼接。
            # 若整组实体都在同一 INSERT scope 内且带局部坐标，就用 local_x/local_y
            # 分行和排序，避开 INSERT rotation/mirror 让 world y 挤在一起的坑。
            use_local_cell = bool(sentence.entities) and all(
                ":insert:" in (item.scope or "")
                and item.local_y is not None
                and item.local_x is not None
                for item in sentence.entities
            )
            row_y = (
                (lambda item: item.local_y) if use_local_cell else (lambda item: item.y)
            )
            row_x = (
                (lambda item: item.local_x) if use_local_cell else (lambda item: item.x)
            )
            # 分行判定：cell_groups 是强合通路，不走 _compute_edge，因此需要在
            # 分行处自带字高/语言硬门槛，避免字高显著不同的中英对照段落被
            # 误判为同一视觉行（例如某印章 block 把 480 高的中文、250 高的英文
            # 和 257 高的英文说明堆在 y 差 20~150 的极近距离，若只按字高 * 0.8
            # 就会拼成一整行）。
            height_tolerance = getattr(settings, "dwg_height_ratio_tolerance", 0.30)

            def _same_visual_row(prev_line: List[TextEntity], candidate: TextEntity) -> bool:
                # 用行内"最后一个"实体作为参考，避免 J G A D I 这种 y 微波动
                # 的字母序列因累积漂移被拆成多段（每个字母比较的是相邻字母，
                # 只要相邻 y 差 <=阈值就归为同一行）。
                anchor = prev_line[-1]
                y_prev = row_y(anchor)
                y_curr = row_y(candidate)
                # y 阈值宽一些（0.7）让同字高的碎片（比如印章顶部逐字母
                # 排列的 "J G A D I"）能合成一行；真正需要拆的场景交给下面
                # 的字高比例硬门槛判定。
                min_h = max(min(anchor.height, candidate.height), 1e-6)
                if abs(y_prev - y_curr) > min_h * 0.7:
                    return False
                # 字高相差超过阈值：视为不同文本流硬断。防止 480 高的中文
                # 名称、250 高的英文对照、257 高的英文说明被 y 挤得很近就拼
                # 成一整行（对应 handle=100A 那类特殊 block 排版）。
                h_max = max(anchor.height, candidate.height, 1e-6)
                h_min = max(min(anchor.height, candidate.height), 1e-6)
                if (h_max - h_min) / h_max > height_tolerance:
                    return False
                return True

            line_groups: List[List[TextEntity]] = []
            for entity in sentence.entities:
                if not line_groups or not _same_visual_row(line_groups[-1], entity):
                    line_groups.append([entity])
                else:
                    line_groups[-1].append(entity)
            sentence.text = "\n".join(
                reconstructor._merge_texts(sorted(line, key=row_x))
                for line in line_groups
            )
            sentences.append(sentence)

        # 参数化引线标注可能被编辑框/引线 barrier 拆成“上行说明 + 下行字段 +
        # 下行 DN 参数”三个 Sentence。它们在语义上是一句完整说明，必须在生成
        # Segment 前确定性合回，避免三个残句分别送给翻译模型。
        inline_diameter_pattern = re.compile(
            r"(?:DN|DE|OD|ID)\s*\d+(?:\.\d+)?",
            re.IGNORECASE,
        )

        def _same_context(first: Sentence, second: Sentence) -> bool:
            first_primary = first.primary_entity
            second_primary = second.primary_entity
            return bool(
                first_primary is not None
                and second_primary is not None
                and first_primary.scope == second_primary.scope
                and first_primary.layer == second_primary.layer
            )

        def _join_annotation_text(left: str, right: str) -> str:
            left = (left or "").rstrip()
            right = (right or "").lstrip()
            if not left or not right:
                return left + right
            left_char = left[-1]
            right_char = right[0]
            needs_space = bool(
                (
                    left_char.isascii()
                    and right_char.isascii()
                    and (
                        left_char.isalnum()
                        or right_char.isalnum()
                        or left_char in ",;:"
                    )
                )
                and not re.search(r"[\u4e00-\u9fff]$", left)
                and not re.match(r"^[\u4e00-\u9fff]", right)
            )
            return left + (" " if needs_space else "") + right

        replacements: dict[int, Sentence] = {}
        consumed_sentence_indices: set[int] = set()
        for label_index, label_sentence in enumerate(sentences):
            if label_index in consumed_sentence_indices:
                continue
            label_text = (label_sentence.text or "").strip()
            label_primary = label_sentence.primary_entity
            if (
                label_primary is None
                or not label_text
                or len(label_text) > 64
                or len(label_sentence.entities) > 2
                or inline_diameter_pattern.search(label_text)
                or not is_translatable_text(label_text)
            ):
                continue

            code_candidates: List[tuple[float, int, Sentence]] = []
            for code_index, code_sentence in enumerate(sentences):
                if code_index == label_index or code_index in consumed_sentence_indices:
                    continue
                code_primary = code_sentence.primary_entity
                if (
                    code_primary is None
                    or not _same_context(label_sentence, code_sentence)
                    or not _is_cad_diameter_block(code_sentence.text)
                ):
                    continue
                average_height = max(
                    (label_primary.height + code_primary.height) / 2.0,
                    1e-6,
                )
                if abs(label_primary.y - code_primary.y) > average_height * 0.8:
                    continue
                if code_primary.x < label_primary.x - average_height * 2.0:
                    continue
                horizontal_gap = code_primary.x - label_primary.right_edge
                if horizontal_gap > average_height * 20.0:
                    continue
                code_candidates.append((
                    abs(label_primary.y - code_primary.y)
                    + abs(horizontal_gap) * 0.05,
                    code_index,
                    code_sentence,
                ))
            if not code_candidates:
                continue
            _, code_index, code_sentence = min(
                code_candidates, key=lambda item: item[0]
            )

            upper_candidates: List[tuple[float, int, Sentence]] = []
            for upper_index, upper_sentence in enumerate(sentences):
                if upper_index in {label_index, code_index}:
                    continue
                if upper_index in consumed_sentence_indices:
                    continue
                upper_primary = upper_sentence.primary_entity
                if (
                    upper_primary is None
                    or not _same_context(label_sentence, upper_sentence)
                    or not inline_diameter_pattern.search(upper_sentence.text or "")
                    or not (upper_sentence.text or "").rstrip().endswith(
                        (",", "，", ";", "；", ":", "：")
                    )
                ):
                    continue
                average_height = max(
                    (upper_primary.height + label_primary.height) / 2.0,
                    1e-6,
                )
                vertical_gap = upper_primary.y - label_primary.y
                if not (average_height * 0.8 < vertical_gap <= average_height * 3.0):
                    continue
                if abs(upper_primary.x - label_primary.x) > average_height * 1.5:
                    continue
                upper_candidates.append((vertical_gap, upper_index, upper_sentence))
            if not upper_candidates:
                continue
            _, upper_index, upper_sentence = min(
                upper_candidates, key=lambda item: item[0]
            )

            merged_entities: List[TextEntity] = []
            seen_entity_handles: set[str] = set()
            for entity in (
                *upper_sentence.entities,
                *label_sentence.entities,
                *code_sentence.entities,
            ):
                if entity.handle in seen_entity_handles:
                    continue
                seen_entity_handles.add(entity.handle)
                merged_entities.append(entity)
            merged_text = _join_annotation_text(
                _join_annotation_text(upper_sentence.text, label_sentence.text),
                code_sentence.text,
            )
            replacements[upper_index] = Sentence(
                sentence_id=upper_sentence.sentence_id,
                text=merged_text,
                entities=merged_entities,
                primary_entity=upper_sentence.primary_entity,
                merge_confidence=min(
                    upper_sentence.merge_confidence,
                    label_sentence.merge_confidence,
                    code_sentence.merge_confidence,
                ),
            )
            consumed_sentence_indices.update({label_index, code_index})
            logger.info(
                "DXF 参数化标注完整句重建：%s + %s + %s -> %s",
                upper_sentence.sentence_id,
                label_sentence.sentence_id,
                code_sentence.sentence_id,
                merged_text[:120],
            )

        if replacements:
            rebuilt_sentences: List[Sentence] = []
            for sentence_index, sentence in enumerate(sentences):
                replacement = replacements.get(sentence_index)
                if replacement is not None:
                    rebuilt_sentences.append(replacement)
                elif sentence_index not in consumed_sentence_indices:
                    rebuilt_sentences.append(sentence)
            sentences = rebuilt_sentences

        # 诊断：合并前后对比
        merged_groups = sum(1 for s in sentences if len(s.entities) > 1)
        logger.info(
            "DXF 语义重建：%d 实体 → %d 句子（合并组 %d，单实体句 %d）",
            len(all_entities), len(sentences), merged_groups, len(sentences) - merged_groups,
        )
        # 拒绝原因统计（在 TextFlowGraph 里累积）
        reject_stats = getattr(reconstructor, "last_reject_stats", None)
        if reject_stats:
            logger.info("DXF 合并拒绝原因统计：%s", dict(reject_stats))

        # 定向诊断：dump 匹配 dwg_debug_text_patterns 的实体及其同 (scope, layer) 邻居
        patterns_raw = getattr(settings, "dwg_debug_text_patterns", "") or ""
        dump_path = (getattr(settings, "dwg_debug_dump_file", "") or "").strip()
        if patterns_raw.strip():
            try:
                dump_re = re.compile(patterns_raw)
            except re.error as exc:
                logger.warning("dwg_debug_text_patterns 正则无效：%s (%s)", patterns_raw, exc)
                dump_re = None
            if dump_re is not None:
                matched_keys: set = set()
                matched_count = 0
                for e in all_entities:
                    if dump_re.search(e.text or ""):
                        matched_keys.add((e.scope, e.layer))
                        matched_count += 1
                logger.info(
                    "DXF 定向诊断：正则 %r 命中 %d 个实体，分布在 %d 个 (scope,layer) 桶",
                    patterns_raw, matched_count, len(matched_keys),
                )

                # 兜底：如果正则没命中任何实体，把所有以"、"开头的短碎片打出来看看
                if not matched_keys:
                    stray = [
                        e for e in all_entities
                        if (e.text or "").strip().startswith("、")
                        or (e.text or "").strip().startswith(", ")
                    ]
                    if stray:
                        logger.info(
                            "DXF 定向诊断：正则没命中，但发现 %d 个以'、'开头的碎片："
                            "前 5 个 handle/scope/layer/text 如下",
                            len(stray),
                        )
                        for e in stray[:5]:
                            logger.info(
                                "  [碎片] %s|%s|%s: %r", e.handle, e.scope, e.layer, e.text[:60],
                            )
                            matched_keys.add((e.scope, e.layer))

                # 命中的实体所在的 sentence（合并组）
                entity_to_sentence: dict = {}
                for sent in sentences:
                    for ent in sent.entities:
                        entity_to_sentence[ent.handle] = sent.sentence_id

                fh = None
                if dump_path:
                    try:
                        import os as _os
                        _os.makedirs(_os.path.dirname(dump_path) or ".", exist_ok=True)
                        fh = open(dump_path, "a", encoding="utf-8")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("无法打开 dwg_debug_dump_file=%s：%s", dump_path, exc)

                try:
                    for key in matched_keys:
                        group = [e for e in all_entities if (e.scope, e.layer) == key]
                        # 按 y 降序、同 y 按 x 升序，方便肉眼读
                        group.sort(key=lambda e: (-round(e.y, 1), e.x))
                        record = {
                            "scope": key[0],
                            "layer": key[1],
                            "entity_count": len(group),
                            "entities": [
                                {
                                    "handle": e.handle,
                                    "entity_type": e.entity_type,
                                    "style": e.style,
                                    "height": round(e.height, 3),
                                    "rotation": round(e.rotation, 2),
                                    "tag": e.tag,
                                    "insert_handle": e.insert_handle,
                                    "x": round(e.x, 2),
                                    "y": round(e.y, 2),
                                    "width": round(e.width, 2),
                                    "text": e.text[:120],
                                    "sentence_id": entity_to_sentence.get(e.handle),
                                }
                                for e in group
                            ],
                        }
                        # 日志里出一行 header + 每条 entity 一行，避免被并发日志淹没时至少还能翻到
                        logger.info(
                            "[定向] scope=%s layer=%s entities=%d",
                            key[0], key[1], len(group),
                        )
                        for ent in record["entities"]:
                            logger.info(
                                "[定向-实体] %s h=%s style=%r H=%.3f y=%.2f x=%.2f text=%r sent=%s",
                                ent["entity_type"], ent["handle"], ent["style"], ent["height"],
                                ent["y"], ent["x"], ent["text"], ent["sentence_id"],
                            )
                        if fh is not None:
                            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                finally:
                    if fh is not None:
                        try:
                            fh.close()
                        except Exception:  # noqa: BLE001
                            pass
                    if dump_path:
                        logger.info("DXF 定向诊断已写入：%s", dump_path)

        # Step 3: 将句子转换为 BlockNode
        nodes: List[BlockNode] = []
        merged_count = 0
        post_filter_stats = {"non_translatable": 0, "dimension_like": 0}

        for sentence in sentences:
            merged_text = (sentence.text or "").strip()
            if not merged_text:
                continue

            # 合并后过滤：整句仍然不含可译字符 / 整句仍是尺寸式，才丢弃。
            # 与前置放行策略配套，保证 "98-2014" 单条不被独立丢弃，
            # 而 "1234-5678" 这种纯尺寸单条会在此处被剔除。
            if skip_non_translatable and not is_translatable_text(merged_text):
                post_filter_stats["non_translatable"] += 1
                logger.debug(
                    "DXF 跳过(合并后非可译) sid=%s text=%r",
                    sentence.sentence_id, merged_text[:80],
                )
                if audit is not None:
                    primary = sentence.primary_entity
                    audit.append({
                        "handle": primary.handle if primary else "",
                        "entity_type": primary.entity_type if primary else "",
                        "layer": sentence.layer,
                        "scope": primary.scope if primary else "",
                        "text": merged_text,
                        "has_chinese": False,
                        "status": "skipped",
                        "reason": "post_merge_non_translatable",
                        "sentence_id": sentence.sentence_id,
                    })
                continue
            if skip_dimension_like and _is_dimension_like(merged_text):
                post_filter_stats["dimension_like"] += 1
                logger.debug(
                    "DXF 跳过(合并后尺寸式) sid=%s text=%r",
                    sentence.sentence_id, merged_text[:80],
                )
                if audit is not None:
                    primary = sentence.primary_entity
                    audit.append({
                        "handle": primary.handle if primary else "",
                        "entity_type": primary.entity_type if primary else "",
                        "layer": sentence.layer,
                        "scope": primary.scope if primary else "",
                        "text": merged_text,
                        "has_chinese": False,
                        "status": "skipped",
                        "reason": "post_merge_dimension_like",
                        "sentence_id": sentence.sentence_id,
                    })
                continue

            is_merged = sentence.is_merged
            if is_merged:
                merged_count += 1
            
            # 构建 metadata
            handles = sentence.handles
            primary = sentence.primary_entity

            def _root_handle(h: str) -> str:
                # 拆段 MTEXT 的伪 handle "<hex>#p<idx>" 还原到原 handle
                return h.split("#p", 1)[0] if "#p" in h else h

            root_primary = _root_handle(primary.handle) if primary else (
                _root_handle(handles[0]) if handles else ""
            )
            root_handles = [_root_handle(h) for h in handles]

            metadata = {
                "entity_type": "MERGED_TEXT" if is_merged else (primary.entity_type if primary else "TEXT"),
                "handle": root_primary,
                "layer": sentence.layer,
                "scope": primary.scope if primary else "",
                # 所有经过 CAD 语义重建并带有几何范围的句段都是文本块；
                # is_merged 只表示它是否由多个底层实体组成，不能再被当成
                # “是否允许按文本框换行”的判据。
                "cad_text_block": True,
                "is_merged": is_merged,
                "merged_handles": root_handles,
                "merged_count": len(handles),
                "sentence_id": sentence.sentence_id,
                # L1/L5：合并信心分数，供前端 mini-map / 灰度提示
                "merge_confidence": round(float(sentence.merge_confidence), 3),
            }

            # MTEXT 拆段标记：若本 sentence 全部实体都是同一原 MTEXT 的拆段，
            # 导出时要在原 y 位置创建独立小 MTEXT，避免各段 y 相对偏移随译文膨胀。
            split_handles = [h for h in handles if "#p" in h]
            if split_handles and len(split_handles) == len(handles):
                parent_roots = {h.split("#p", 1)[0] for h in split_handles}
                if len(parent_roots) == 1:
                    parent_handle = parent_roots.pop()
                    metadata["mtext_split_parent"] = parent_handle
                    metadata["mtext_split_layout_version"] = 3
                    metadata["mtext_split_indices"] = sorted(
                        int(h.split("#p", 1)[1]) for h in split_handles
                    )
                    # 高度预算：本段 y 到"下一段起始 y"的距离，导出时用它决定
                    # 译文过长是否需要缩字号，避免翻译后向下延伸砸到图表/表格。
                    parent_next_y = self._mtext_split_next_y.get(
                        parent_handle, {}
                    ).get(min(metadata["mtext_split_indices"]))
                    if parent_next_y is not None and primary is not None:
                        budget = float(primary.y) - float(parent_next_y)
                        if budget > 0:
                            metadata["mtext_split_y_budget"] = budget
            
            if primary:
                metadata["primary_x"] = primary.x
                metadata["primary_y"] = primary.y
                metadata["primary_height"] = primary.height
                metadata["primary_style"] = primary.style
                metadata["primary_color"] = primary.color
                metadata["primary_true_color"] = primary.true_color
                metadata["primary_transparency"] = primary.transparency

            if sentence.entities:
                metadata["group_x"] = min(e.x for e in sentence.entities)
                metadata["group_y_top"] = max(e.y + e.height for e in sentence.entities)
                metadata["group_width"] = max(e.right_edge for e in sentence.entities) - metadata["group_x"]
                metadata["group_height"] = metadata["group_y_top"] - min(e.y for e in sentence.entities)

                # 原图正文通常使用首行缩进：首行 TEXT 的 x 大于后续行左边界。
                # MTEXT 重建必须显式保存该偏移，否则译文会全部贴到 group_x。
                top_y = max(e.y for e in sentence.entities)
                average_height = max(
                    sum(max(e.height, 1e-6) for e in sentence.entities)
                    / len(sentence.entities),
                    1e-6,
                )
                first_line_entities = [
                    e for e in sentence.entities
                    if abs(e.y - top_y) <= average_height * 0.8
                ]
                first_line_x = min(
                    (e.x for e in first_line_entities),
                    default=metadata["group_x"],
                )
                metadata["group_first_line_indent"] = max(
                    first_line_x - metadata["group_x"],
                    0.0,
                )
                metadata["cad_visual_heading"] = bool(
                    len(sentence.entities) <= 2
                    and len((sentence.text or "").strip()) <= 64
                    and re.match(
                        r"^(?:[一二三四五六七八九十百]+[、．.]|"
                        r"[IVXLCDM]+[．.]|\d+(?:\.\d+)*[、．.])",
                        (sentence.text or "").strip(),
                        re.IGNORECASE,
                    )
                )

            cell = cell_by_entity_id.get(id(primary)) if primary is not None else None
            if cell is not None:
                left, right, bottom, top = cell
                cell_width = max(right - left, 1e-6)
                cell_height = max(top - bottom, 1e-6)
                padding = min(
                    max(primary.height * 0.5, 1e-6),
                    cell_width * 0.1,
                    cell_height * 0.1,
                )
                metadata["cad_table_cell"] = True
                metadata["group_x"] = left + padding
                metadata["group_y_top"] = top - padding
                metadata["group_width"] = max(cell_width - padding * 2, 1e-6)
                metadata["group_height"] = max(cell_height - padding * 2, 1e-6)
            
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
                    # INSERT 内实体保留 block 局部坐标，供导出/诊断时区分
                    # 世界坐标与 block 内部原始坐标（rotation/mirror 后者更稳定）。
                    "local_x": e.local_x,
                    "local_y": e.local_y,
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
            "DXF 语义重建完成：%d 个原始实体 → %d 个句子（其中 %d 个合并组），"
            "另有 %d 个独立节点（DIMENSION/MULTILEADER/ACAD_TABLE）；"
            "合并后兜底过滤：非可译=%d，尺寸式=%d",
            len(all_entities),
            len(nodes),
            merged_count,
            len(standalone_nodes),
            post_filter_stats["non_translatable"],
            post_filter_stats["dimension_like"],
        )

        # 独立节点（DIMENSION / MULTILEADER / ACAD_TABLE）不参与合并，直接追加
        if standalone_nodes and audit is not None:
            for sn in standalone_nodes:
                text = sn.text_content or ""
                audit.append({
                    "handle": sn.metadata.get("handle", ""),
                    "entity_type": sn.metadata.get("entity_type", ""),
                    "layer": sn.metadata.get("layer", ""),
                    "scope": sn.metadata.get("scope", ""),
                    "text": text,
                    "has_chinese": bool(re.search(r"[\u4e00-\u9fff]", text)),
                    "status": "kept",
                    "reason": "standalone",
                })

        # L5-LLM 二次校验（可选）：对灰区合并句一次性问 LLM 是否真的是一句
        try:
            if getattr(settings, "dwg_llm_verify_enabled", False):
                from app.services.adapters.dwg_llm_verifier import verify_and_split_sentences
                nodes = verify_and_split_sentences(nodes)
        except Exception as exc:  # noqa: BLE001 - 兜底：任何异常都不阻断解析
            logger.warning("L5-LLM 二次校验失败(容错跳过)：%s", exc)

        return nodes + standalone_nodes

    def _split_mtext_paragraphs(
            self,
            entity,
            scope: str,
            *,
            transform=None,
            insert_handle: str = "",
            block_name: str = "",
        ) -> Optional[List["TextEntity"]]:
            """按 \\P 拆分带大段视觉空白的 MTEXT，并保留原始纵向布局。"""
            if not hasattr(self, "_mtext_split_next_y"):
                self._mtext_split_next_y = {}
            raw = entity.text or ""
            if not raw:
                return None
            cleaned = clean_mtext(raw)
            parts = [p.strip() for p in cleaned.split("\n")]
            if len([p for p in parts if p]) <= 1:
                return None

            max_gap = 0
            current_gap = 0
            for part in parts:
                if part:
                    current_gap = 0
                else:
                    current_gap += 1
                    max_gap = max(max_gap, current_gap)
            if max_gap < 2:
                return None

            base = self._extract_text_entity(
                entity,
                scope,
                transform=transform,
                insert_handle=insert_handle,
                block_name=block_name,
            )
            if base is None:
                return None

            # MTEXT 可用 \\H...; 覆盖实体标称字高；2C9A 使用 \\H1.1666x，
            # 忽略该倍率会把图表预留空段压缩，导致 9.2 提前落到图形上方。
            nominal_height = base.height if base.height > 0 else 2.5
            local_nominal_height = float(
                getattr(entity.dxf, "char_height", nominal_height) or nominal_height
            )
            local_heights = _mtext_paragraph_heights(raw, local_nominal_height)
            height_scale = nominal_height / max(local_nominal_height, 1e-6)
            paragraph_heights = [height * height_scale for height in local_heights]
            first_height = paragraph_heights[0] if paragraph_heights else nominal_height
            spacing_factor = float(
                getattr(entity.dxf, "line_spacing_factor", 1.0) or 1.0
            )
            available_width = base.width if base.width > 0 else first_height * 30

            # _extract_text_entity 按标称字高换算 top/middle attachment；这里补上
            # 内嵌有效字高产生的差值，使首段顶部仍与原 MTEXT insert 对齐。
            attachment = int(getattr(entity.dxf, "attachment_point", 1) or 1)
            if attachment in (1, 2, 3):
                cursor_y = base.y - (first_height - nominal_height)
            elif attachment in (4, 5, 6):
                cursor_y = base.y - (first_height - nominal_height) / 2.0
            else:
                cursor_y = base.y

            entities: List[TextEntity] = []
            budget_boundary_by_idx: Dict[int, float] = {}
            for idx, part in enumerate(parts):
                effective_height = (
                    paragraph_heights[idx]
                    if idx < len(paragraph_heights)
                    else first_height
                )
                line_h = effective_height * (5.0 / 3.0) * spacing_factor
                if not part:
                    cursor_y -= line_h
                    continue

                para_y = cursor_y
                entities.append(TextEntity(
                    handle=f"{base.handle}#p{idx}",
                    entity_type="MTEXT",
                    layer=base.layer,
                    text=part,
                    x=base.x,
                    y=para_y,
                    height=effective_height,
                    width=base.width,
                    rotation=base.rotation,
                    style=base.style,
                    scope=base.scope,
                    block_name=base.block_name,
                    tag=base.tag,
                    insert_handle=base.insert_handle,
                    bbox_source=f"{base.bbox_source}+split",
                    color=base.color,
                    true_color=base.true_color,
                    transparency=base.transparency,
                ))

                # 显式 \P 之外，MTEXT 还会按 box width 自动折行。后续段落必须从
                # 原段实际占用行数之后开始，否则 9.2/9.3 会落在同一行并相互覆盖。
                source_width = estimate_text_width(part, effective_height)
                source_lines = max(1, math.ceil(source_width / max(available_width, 1e-6)))
                occupied_height = source_lines * line_h
                cursor_y -= occupied_height

                # 预算只覆盖本段原本占用的文本槽，不把连续空行（图表预留区）
                # 算进去，避免英文译文扩张后覆盖中间的表格和示意图。
                budget_boundary_by_idx[idx] = para_y - occupied_height

            self._mtext_split_next_y[base.handle] = {
                idx: boundary for idx, boundary in budget_boundary_by_idx.items()
            }
            return entities


    def _extract_text_entity(
        self,
        entity,
        scope: str,
        *,
        transform=None,
        insert_handle: str = "",
        block_name: str = "",
    ) -> Optional[TextEntity]:
        """从 ezdxf 实体中提取 TextEntity 信息（L0 版本）。

        - x/y 使用锚点（TEXT: halign/valign→align_point；MTEXT: attachment_point 反推左下）
        - width 用估算（后续 phase 可换成 bbox）
        - height 始终使用**标称字高**（TEXT.height / MTEXT.char_height），
          绝不用 `entity.bbox()` 的渲染高度——那个高度依赖字形是否含下沉，
          会让同一字号的文字之间高度差看似 30%+，反而把 L4 合并门槛卡死。
        - 处于 INSERT 内部时，(x, y) 通过父级 transform 变换到世界坐标，
          width/height 按 transform 的 x/y 缩放系数一起放大。
        """
        dxftype = entity.dxftype()
        if dxftype not in ("TEXT", "MTEXT", "ATTRIB", "ATTDEF"):
            return None

        handle = getattr(entity.dxf, "handle", "")
        layer = getattr(entity.dxf, "layer", "0")
        style = getattr(entity.dxf, "style", "") or ""
        raw_color = getattr(entity.dxf, "color", 256)
        color = int(raw_color) if raw_color is not None else 256
        raw_true_color = getattr(entity.dxf, "true_color", None)
        true_color = int(raw_true_color) if raw_true_color is not None else None
        raw_transparency = getattr(entity.dxf, "transparency", None)
        transparency = int(raw_transparency) if raw_transparency is not None else None

        if dxftype == "TEXT":
            text = getattr(entity.dxf, "text", "") or ""
            nominal_height = float(getattr(entity.dxf, "height", 2.5) or 2.5)
            rotation = float(getattr(entity.dxf, "rotation", 0.0) or 0.0)
            width_factor = float(getattr(entity.dxf, "width", 1.0) or 1.0)
            local_x, local_y = self._text_anchor(entity)
            local_width = estimate_text_width(text, nominal_height, 0.6 * width_factor)
            tag = ""
        elif dxftype == "MTEXT":
            raw = entity.text or ""
            text = clean_mtext(raw)
            nominal_height = float(getattr(entity.dxf, "char_height", 2.5) or 2.5)
            rotation = float(getattr(entity.dxf, "rotation", 0.0) or 0.0)
            local_x, local_y = self._mtext_anchor(entity, char_height=nominal_height)
            mtext_width = float(getattr(entity.dxf, "width", 0) or 0)
            local_width = mtext_width if mtext_width > 0 else estimate_text_width(text, nominal_height)
            tag = ""
        else:  # ATTRIB / ATTDEF
            text = getattr(entity.dxf, "text", "") or ""
            nominal_height = float(getattr(entity.dxf, "height", 2.5) or 2.5)
            rotation = float(getattr(entity.dxf, "rotation", 0.0) or 0.0)
            width_factor = float(getattr(entity.dxf, "width", 1.0) or 1.0)
            local_x, local_y = self._text_anchor(entity)
            local_width = estimate_text_width(text, nominal_height, 0.6 * width_factor)
            tag = (getattr(entity.dxf, "tag", "") or "").strip()

        if not text.strip():
            return None

        if transform is not None:
            world_x, world_y = self._transform_point(local_x, local_y, transform)
            sx, sy = self._transform_scales(transform)
            world_width = local_width * sx
            world_height = nominal_height * sy
            world_rotation = rotation + self._transform_rotation_deg(transform)
            bbox_source = "align"
            # 只有落在 INSERT 内部（transform 非 None）才保存 block 局部坐标，
            # 用于后续同 INSERT scope 的归行判断，避开 rotation/mirror 打乱 y。
            local_coord_x: Optional[float] = float(local_x)
            local_coord_y: Optional[float] = float(local_y)
            local_coord_width: Optional[float] = float(local_width) if local_width else None
        else:
            world_x, world_y = local_x, local_y
            world_width = local_width
            world_height = nominal_height
            world_rotation = rotation
            bbox_source = "nominal"
            local_coord_x = None
            local_coord_y = None
            local_coord_width = None

        return TextEntity(
            handle=handle,
            entity_type=dxftype,
            layer=layer,
            text=text.strip(),
            x=world_x,
            y=world_y,
            height=world_height if world_height > 0 else nominal_height,
            width=world_width if world_width > 0 else local_width,
            rotation=world_rotation,
            style=style,
            scope=scope,
            block_name=block_name,
            tag=tag,
            insert_handle=insert_handle,
            bbox_source=bbox_source,
            color=color,
            true_color=true_color,
            transparency=transparency,
            local_x=local_coord_x,
            local_y=local_coord_y,
            local_width=local_coord_width,
        )

    # ------------------------------------------------------------------
    # L0 helpers：真 bbox / 对齐锚点 / INSERT 仿射变换
    # ------------------------------------------------------------------

    @staticmethod
    def _text_anchor(entity) -> tuple[float, float]:
        """TEXT/ATTRIB 的绘制起点：非左下对齐时优先用 align_point。"""
        halign = int(getattr(entity.dxf, "halign", 0) or 0)
        valign = int(getattr(entity.dxf, "valign", 0) or 0)
        try:
            if (halign != 0 or valign != 0):
                align_pt = getattr(entity.dxf, "align_point", None)
                if align_pt is not None:
                    return float(align_pt[0]), float(align_pt[1])
        except Exception:  # noqa: BLE001
            pass
        try:
            ins = entity.dxf.insert
            return float(ins[0]), float(ins[1])
        except Exception:  # noqa: BLE001
            return 0.0, 0.0

    @staticmethod
    def _mtext_anchor(entity, *, char_height: float) -> tuple[float, float]:
        """MTEXT 的左下锚点：根据 attachment_point 从 insert 反推。

        attachment_point:
          1 top-left  2 top-center  3 top-right
          4 middle-left 5 middle-center 6 middle-right
          7 bottom-left 8 bottom-center 9 bottom-right
        """
        try:
            ins = entity.dxf.insert
            ix, iy = float(ins[0]), float(ins[1])
        except Exception:  # noqa: BLE001
            return 0.0, 0.0
        try:
            ap = int(getattr(entity.dxf, "attachment_point", 1) or 1)
        except Exception:  # noqa: BLE001
            ap = 1
        # 只在垂直方向做补偿，MTEXT 的水平锚点是绘制左边缘，用于比较左对齐已足够
        if ap in (1, 2, 3):        # top-*
            iy -= char_height
        elif ap in (4, 5, 6):      # middle-*
            iy -= char_height / 2.0
        # bottom-* 就是左下（近似）
        return ix, iy

    @staticmethod
    def _transform_point(x: float, y: float, transform) -> tuple[float, float]:
        """把局部 (x,y) 经过 Matrix44 变换到世界坐标。"""
        try:
            tx, ty, _ = transform.transform((float(x), float(y), 0.0))
            return float(tx), float(ty)
        except Exception:  # noqa: BLE001
            return float(x), float(y)

    @staticmethod
    def _transform_scales(transform) -> tuple[float, float]:
        """从 Matrix44 里估算 x/y 方向的整体缩放系数（含嵌套 INSERT）。"""
        try:
            # 基向量在变换下的长度即为缩放
            x_axis = transform.transform_direction((1.0, 0.0, 0.0))
            y_axis = transform.transform_direction((0.0, 1.0, 0.0))
            sx = math.hypot(x_axis[0], x_axis[1]) or 1.0
            sy = math.hypot(y_axis[0], y_axis[1]) or 1.0
            return sx, sy
        except Exception:  # noqa: BLE001
            return 1.0, 1.0

    @staticmethod
    def _transform_rotation_deg(transform) -> float:
        """从 Matrix44 里取 X 轴的整体旋转角度（度）。"""
        try:
            x_axis = transform.transform_direction((1.0, 0.0, 0.0))
            return math.degrees(math.atan2(x_axis[1], x_axis[0]))
        except Exception:  # noqa: BLE001
            return 0.0

    @staticmethod
    def _make_insert_transform(insert_entity, parent=None):
        """把 INSERT 的 (insert, xscale, yscale, rotation) 折成一个 Matrix44，
        并叠加父级 transform（用于嵌套 INSERT）。拿不到 ezdxf 时返回 None。
        """
        try:
            from ezdxf.math import Matrix44
        except Exception:  # noqa: BLE001
            return None
        try:
            ins = insert_entity.dxf.insert
            ix, iy = float(ins[0]), float(ins[1])
            sx = float(getattr(insert_entity.dxf, "xscale", 1.0) or 1.0)
            sy = float(getattr(insert_entity.dxf, "yscale", 1.0) or 1.0)
            rot = float(getattr(insert_entity.dxf, "rotation", 0.0) or 0.0)
        except Exception:  # noqa: BLE001
            return parent
        # 先缩放，再旋转，再平移到 insert 点
        m = Matrix44.chain(
            Matrix44.scale(sx, sy, 1.0),
            Matrix44.z_rotate(math.radians(rot)),
            Matrix44.translate(ix, iy, 0.0),
        )
        if parent is not None:
            m = Matrix44.chain(m, parent)
        return m



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
