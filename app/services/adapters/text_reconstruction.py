"""
CAD 文本语义重建模块 (Text Reconstruction)

核心思路：
1. 不做"几何合并实体"，而是做"逻辑合并文本内容"
2. 按"阅读顺序"重建句子：同一 baseline + 同一方向 + 距离在阈值内
3. 建立 sentence-level grouping，翻译在句子级做
4. 回填时用单一 MTEXT 重新生成，删除原碎片实体

文本流图（Text Flow Graph）：
- node = text entity
- edge = spatial continuity
- path = 最可能的一句文本路径
"""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class BarrierLine:
    """L2 网格线阻挡用的一条几何线段。已在世界坐标下且已按方向分类。"""
    axis: str  # "h" 水平线 / "v" 垂直线
    # 水平线：pos = y，range_min/range_max = x 范围
    # 垂直线：pos = x，range_min/range_max = y 范围
    pos: float
    range_min: float
    range_max: float
    scope: str = ""


class BarrierIndex:
    """按 scope 组织的水平/垂直线索引，用于"穿越检测"。

    - 数据规模：一张图上千条线是常态，因此按 scope+方向分桶再按 pos 排序，
      查询时用 bisect 把候选缩到"在 y/x 带内"，再线性看是否覆盖目标区间。
    - 只当"命中即拒"用，不参与打分，逻辑简单也不需要精确交点。
    """

    def __init__(self) -> None:
        self._h: Dict[str, List[BarrierLine]] = {}
        self._v: Dict[str, List[BarrierLine]] = {}
        self._sorted = False

    def add(self, line: BarrierLine) -> None:
        bucket = self._h if line.axis == "h" else self._v
        bucket.setdefault(line.scope, []).append(line)
        self._sorted = False

    def finalize(self) -> None:
        for bucket in (self._h, self._v):
            for lines in bucket.values():
                lines.sort(key=lambda ln: ln.pos)
        self._sorted = True

    def horizontal_between(
        self, scope: str, y1: float, y2: float, x_min: float, x_max: float
    ) -> Optional[BarrierLine]:
        """在 y 区间内寻找覆盖 [x_min, x_max] 的水平线；返回首个命中或 None。"""
        if not self._sorted:
            self.finalize()
        lo, hi = (y1, y2) if y1 <= y2 else (y2, y1)
        lines = self._h.get(scope)
        if not lines:
            return None
        # bisect 找出 pos 在 [lo, hi] 的候选区间
        from bisect import bisect_left, bisect_right
        positions = [ln.pos for ln in lines]
        i = bisect_left(positions, lo)
        j = bisect_right(positions, hi)
        for ln in lines[i:j]:
            if ln.range_min <= x_min and ln.range_max >= x_max:
                return ln
            # 部分覆盖也算：任意端点落入 x 范围 → 视为屏障
            if ln.range_min <= x_max and ln.range_max >= x_min:
                # 覆盖 x 范围的至少 50% 才算真挡住，避免"擦边线"误伤
                covered = min(ln.range_max, x_max) - max(ln.range_min, x_min)
                if covered >= 0.5 * (x_max - x_min):
                    return ln
        return None

    def vertical_between(
        self, scope: str, x1: float, x2: float, y_min: float, y_max: float
    ) -> Optional[BarrierLine]:
        """在 x 区间内寻找覆盖 [y_min, y_max] 的垂直线；返回首个命中或 None。"""
        if not self._sorted:
            self.finalize()
        lo, hi = (x1, x2) if x1 <= x2 else (x2, x1)
        lines = self._v.get(scope)
        if not lines:
            return None
        from bisect import bisect_left, bisect_right
        positions = [ln.pos for ln in lines]
        i = bisect_left(positions, lo)
        j = bisect_right(positions, hi)
        for ln in lines[i:j]:
            if ln.range_min <= y_min and ln.range_max >= y_max:
                return ln
            if ln.range_min <= y_max and ln.range_max >= y_min:
                covered = min(ln.range_max, y_max) - max(ln.range_min, y_min)
                if covered >= 0.5 * (y_max - y_min):
                    return ln
        return None


class EntityPriority(Enum):
    """实体优先级：MTEXT > ATTRIB > TEXT"""
    MTEXT = 1      # 内部本来就是段落
    ATTRIB = 2     # 块内语义
    TEXT = 3       # 碎片


@dataclass
class TextEntity:
    """文本实体的完整信息

    L0 后 x/y 语义收敛为"世界坐标下的左下角"，width/height 为世界坐标下的
    包围盒尺寸（已含 INSERT 缩放）。在拿不到真实 bbox 时会退化到估算值。
    """
    handle: str
    entity_type: str  # TEXT, MTEXT, ATTRIB, ATTDEF
    layer: str
    text: str
    x: float
    y: float
    height: float
    width: float = 0.0
    rotation: float = 0.0
    style: str = ""
    scope: str = ""
    block_name: str = ""  # INSERT 所属的块名
    # L4 逻辑分组用
    tag: str = ""           # ATTRIB/ATTDEF 的 tag
    insert_handle: str = "" # 所属 INSERT 的 handle（区分同 BLOCK 的多次实例化）
    bbox_source: str = ""   # "ezdxf" | "align" | "estimate"，仅用于调试
    
    @property
    def priority(self) -> int:
        if self.entity_type == "MTEXT":
            return EntityPriority.MTEXT.value
        elif self.entity_type == "ATTRIB":
            return EntityPriority.ATTRIB.value
        return EntityPriority.TEXT.value
    
    @property
    def right_edge(self) -> float:
        """右边界 X 坐标"""
        return self.x + self.width
    
    @property
    def baseline(self) -> float:
        """基线 Y 坐标（简化处理，取 y）"""
        return self.y


@dataclass 
class TextFlowEdge:
    """文本流图的边：表示两个实体之间的空间连续性"""
    source: str  # source handle
    target: str  # target handle
    x_gap: float  # X 方向间隔
    y_diff: float  # Y 方向差异
    rotation_diff: float  # 旋转角度差异
    score: float = 0.0  # 连续性得分（越高越可能是同一句）


@dataclass
class Sentence:
    """重建后的句子"""
    sentence_id: str
    text: str  # 合并后的完整文本
    entities: List[TextEntity] = field(default_factory=list)
    primary_entity: Optional[TextEntity] = None  # 主实体（用于定位）
    # L1/L5 输出：本句合并信心分数 (0-1)。单实体句为 1.0，合并句取簇内最弱边分数。
    merge_confidence: float = 1.0

    # 导出时需要的信息
    @property
    def handles(self) -> List[str]:
        return [e.handle for e in self.entities]
    
    @property
    def is_merged(self) -> bool:
        return len(self.entities) > 1
    
    @property
    def layer(self) -> str:
        return self.primary_entity.layer if self.primary_entity else "0"
    
    @property
    def position(self) -> Tuple[float, float]:
        if self.primary_entity:
            return (self.primary_entity.x, self.primary_entity.y)
        return (0.0, 0.0)
    
    @property
    def height(self) -> float:
        if self.primary_entity:
            return self.primary_entity.height
        return 2.5


class TextFlowGraph:
    """文本流图：用图结构表示文本实体之间的空间关系"""

    def __init__(
        self,
        y_threshold_factor: float = 0.8,
        x_gap_threshold_factor: float = 3.0,
        rotation_threshold: float = 5.0,
        enable_semantic_break: bool = True,  # 方案2：语义分割判断
        enable_logical_grouping: bool = True,  # L4：按 style/height/tag/INSERT 做逻辑分组
        height_ratio_tolerance: float = 0.30,  # 字高相差比例 <= 30% 视为一致（中英混排常有 20%）
        next_line_gap_factor: float = 3.0,     # 换行合并 y 阈值倍数（有 L2 挡表格后可放宽）
        barrier_index: Optional[BarrierIndex] = None,  # L2 网格线阻挡
        enable_greedy_merge: bool = True,      # L1：按打分贪心合并，拒绝桥接式误合
        min_edge_score: float = 0.15,          # 弱边分数下限，低于此值直接丢弃
        iou_split_threshold: float = 0.5,      # L3：bbox IoU 超过此值直接拒（重叠标注不合并）
    ):
        self.y_threshold_factor = y_threshold_factor
        self.x_gap_threshold_factor = x_gap_threshold_factor
        self.rotation_threshold = rotation_threshold
        self.enable_semantic_break = enable_semantic_break
        self.enable_logical_grouping = enable_logical_grouping
        self.height_ratio_tolerance = height_ratio_tolerance
        self.next_line_gap_factor = next_line_gap_factor
        self.barriers = barrier_index
        self.enable_greedy_merge = enable_greedy_merge
        self.min_edge_score = min_edge_score
        self.iou_split_threshold = iou_split_threshold

        self.nodes: Dict[str, TextEntity] = {}
        self.edges: List[TextFlowEdge] = []
        self.adjacency: Dict[str, List[str]] = {}
        # 诊断：候选对中每种拒绝原因的次数
        self.reject_reasons: Dict[str, int] = {}
        # L1/L5：每条 path（按其首个 handle 索引）的合并信心分数
        self.path_confidence: Dict[str, float] = {}

    def _reject(self, reason: str) -> None:
        self.reject_reasons[reason] = self.reject_reasons.get(reason, 0) + 1

    @staticmethod
    def _bbox_iou(e1: "TextEntity", e2: "TextEntity") -> float:
        """两个实体 bbox 的 IoU。宽度是 estimate 的，只当作粗略判据用。"""
        a_l, a_b, a_r, a_t = e1.x, e1.y, e1.right_edge, e1.y + e1.height
        b_l, b_b, b_r, b_t = e2.x, e2.y, e2.right_edge, e2.y + e2.height
        inter_w = min(a_r, b_r) - max(a_l, b_l)
        inter_h = min(a_t, b_t) - max(a_b, b_b)
        if inter_w <= 0 or inter_h <= 0:
            return 0.0
        inter = inter_w * inter_h
        a_area = max((a_r - a_l) * (a_t - a_b), 1e-6)
        b_area = max((b_r - b_l) * (b_t - b_b), 1e-6)
        union = a_area + b_area - inter
        if union <= 0:
            return 0.0
        return inter / union
    
    def add_entity(self, entity: TextEntity) -> None:
        """添加实体节点"""
        self.nodes[entity.handle] = entity
        if entity.handle not in self.adjacency:
            self.adjacency[entity.handle] = []
    
    def build_edges(self) -> None:
        """构建边：计算所有实体对之间的空间连续性"""
        handles = list(self.nodes.keys())
        self.reject_reasons["accepted"] = 0

        for i, h1 in enumerate(handles):
            for h2 in handles[i+1:]:
                e1 = self.nodes[h1]
                e2 = self.nodes[h2]

                edge = self._compute_edge(e1, e2)
                if edge and edge.score > 0:
                    self.edges.append(edge)
                    self.adjacency[h1].append(h2)
                    self.adjacency[h2].append(h1)
                    self.reject_reasons["accepted"] = self.reject_reasons.get("accepted", 0) + 1

    def _compute_edge(
        self, e1: TextEntity, e2: TextEntity
    ) -> Optional[TextFlowEdge]:
        """计算两个实体之间的边
        
        支持两种合并模式：
        1. 同行合并：Y 接近，X 有间隔（水平方向阅读）
        2. 换行合并：Y 差 1-2 倍字高，X 起点接近（垂直方向换行）
        
        方案2 语义分割：如果前一个文本以句号结尾，且后一个文本以大写/中文开头，不合并
        """
        # 不同图层不合并
        if e1.layer != e2.layer:
            self._reject("layer")
            return None

        # 不同 scope 不合并
        if e1.scope != e2.scope:
            self._reject("scope")
            return None

        # L3：bbox IoU 重叠分离
        # 两段文字如果显著重叠，通常是"标注压图" / "重复标注互相覆盖"，
        # 强行合并会得到语义错乱的句子；这类关系走独立节点更稳。
        # 允许字符级微重叠（IoU < 阈值），只拒真的"叠在一起"。
        iou = self._bbox_iou(e1, e2)
        if iou >= self.iou_split_threshold:
            self._reject("iou_overlap")
            logger.debug(
                "L3 拒绝(IoU=%.2f≥%.2f) %s(%r) vs %s(%r)",
                iou, self.iou_split_threshold,
                e1.handle, e1.text[:12], e2.handle, e2.text[:12],
            )
            return None

        # L4 逻辑分组：CAD 语义硬门槛（在几何合并之前先过一遍）
        # 注意 style / height 只做"软"门槛：CAD 里"中文用 STANDARD、ASCII 用 Arial"
        # 且"数字/字母字高比中文小 20%"是极常见的排版，绝对禁止合并会导致同一行
        # 完全断开。真正需要拆开的是"标题 vs 引注"这种字高相差 >50% 的情况。
        style_penalty = False  # 记录是否有 style 差异，用于后续降分
        if self.enable_logical_grouping:
            if e1.style and e2.style and e1.style != e2.style:
                # 只降分，不直接断——放到 x_gap 校验后再决定
                style_penalty = True

            if e1.height > 0 and e2.height > 0:
                h_ratio = abs(e1.height - e2.height) / max(e1.height, e2.height)
                if h_ratio > self.height_ratio_tolerance:
                    self._reject("height")
                    logger.debug(
                        "L4 拒绝(height差异 %.1f%%>%.0f%%) %s(%r,h=%.3f) vs %s(%r,h=%.3f)",
                        h_ratio * 100, self.height_ratio_tolerance * 100,
                        e1.handle, e1.text[:12], e1.height,
                        e2.handle, e2.text[:12], e2.height,
                    )
                    return None

            # ATTRIB 类硬约束保留：这类混合 100% 是错的
            e1_is_attr = e1.entity_type in ("ATTRIB", "ATTDEF")
            e2_is_attr = e2.entity_type in ("ATTRIB", "ATTDEF")
            if e1_is_attr != e2_is_attr:
                self._reject("attrib_vs_text")
                return None
            if e1_is_attr and e2_is_attr:
                if e1.tag != e2.tag:
                    self._reject("tag")
                    return None
                if e1.insert_handle and e2.insert_handle and e1.insert_handle != e2.insert_handle:
                    self._reject("insert_instance")
                    return None

        avg_height = (e1.height + e2.height) / 2
        
        # 旋转角度差异检查
        rotation_diff = abs(e1.rotation - e2.rotation)
        if rotation_diff > 180:
            rotation_diff = 360 - rotation_diff
        if rotation_diff > self.rotation_threshold:
            self._reject("rotation")
            return None
        
        y_diff = abs(e1.y - e2.y)
        
        # 模式1：同行合并（Y 接近）
        y_threshold_same_line = avg_height * self.y_threshold_factor
        is_same_line = y_diff <= y_threshold_same_line
        
        # 模式2：换行合并（Y 差 1-x 倍字高，X 范围有重叠或起点相近）
        y_threshold_next_line = avg_height * self.next_line_gap_factor
        is_next_line = y_threshold_same_line < y_diff <= y_threshold_next_line

        if is_next_line:
            e1_left, e1_right = e1.x, e1.right_edge
            e2_left, e2_right = e2.x, e2.right_edge
            x_overlap = min(e1_right, e2_right) - max(e1_left, e2_left)
            x_start_diff = abs(e1.x - e2.x)
            x_start_close = x_start_diff < avg_height * 3

            if x_overlap <= 0 and not x_start_close:
                self._reject("next_line_x_apart")
                return None
        elif not is_same_line:
            self._reject("y_gap_too_large")
            return None

        # L2 网格线阻挡：真表格的相邻单元格会被横线/竖线隔开
        if self.barriers is not None:
            if is_next_line:
                # 上下之间是否有横线覆盖两段的水平投影
                x_min = min(e1.x, e2.x)
                x_max = max(e1.right_edge, e2.right_edge)
                if self.barriers.horizontal_between(
                    e1.scope, e1.y, e2.y, x_min, x_max,
                ) is not None:
                    self._reject("h_barrier")
                    return None
            if is_same_line:
                # 左右之间是否有竖线覆盖两段的垂直投影
                y_min = min(e1.y, e2.y)
                y_max = max(e1.y + e1.height, e2.y + e2.height)
                left_x = min(e1.x, e2.x)
                right_x = max(e1.x, e2.x)
                if self.barriers.vertical_between(
                    e1.scope, left_x, right_x, y_min, y_max,
                ) is not None:
                    self._reject("v_barrier")
                    return None

        # L5 语义关系分类：hard_break 直接拒；soft_continue 后面加分
        semantic_bonus = 0.0
        if self.enable_semantic_break:
            left_entity = e1 if e1.x <= e2.x else e2
            right_entity = e2 if e1.x <= e2.x else e1
            relation = self._classify_semantic_relation(
                left_entity.text, right_entity.text,
            )
            if relation == self.SEMANTIC_HARD_BREAK:
                self._reject("semantic_break")
                return None
            if relation == self.SEMANTIC_SOFT_CONTINUE:
                semantic_bonus = 0.15

        # X 间隔计算（确保 e1 在 e2 左边）
        if e1.x > e2.x:
            e1, e2 = e2, e1

        x_gap = e2.x - e1.right_edge
        x_threshold = avg_height * self.x_gap_threshold_factor

        if is_same_line:
            # 允许 estimate_text_width 高估导致的"虚假重叠"：
            # 只有当重叠深度真的超过了较短一段整体宽度的 60%，才算是几何上的真重叠。
            # 这既能挡住"两段文字真的叠在一起"的情况，又能容忍估算误差。
            shorter = min(e1.width, e2.width) if e1.width > 0 and e2.width > 0 else avg_height
            allowed_overlap = max(avg_height * 2.0, shorter * 0.6)
            if x_gap < -allowed_overlap:
                self._reject("x_overlap_too_deep")
                return None
            if x_gap > x_threshold:
                self._reject("x_gap_too_large")
                return None
        
        # 计算连续性得分
        if is_same_line:
            y_score = 1.0 - (y_diff / y_threshold_same_line) if y_threshold_same_line > 0 else 1.0
            x_score = 1.0 - (max(0, x_gap) / x_threshold) if x_threshold > 0 else 1.0
        else:
            # 换行合并，得分略低
            y_score = 0.7 * (1.0 - (y_diff - y_threshold_same_line) / (y_threshold_next_line - y_threshold_same_line))
            x_score = 0.8
        
        r_score = 1.0 - (rotation_diff / self.rotation_threshold) if self.rotation_threshold > 0 else 1.0

        score = (y_score * 0.4 + x_score * 0.4 + r_score * 0.2)
        # L4：style 差异降分（软门槛）
        if style_penalty:
            score -= 0.3
            if score <= 0:
                self._reject("style")
                logger.debug(
                    "L4 拒绝(style差异+分数不足) %s(%r,%s) vs %s(%r,%s) score=%.2f",
                    e1.handle, e1.text[:12], e1.style,
                    e2.handle, e2.text[:12], e2.style, score,
                )
                return None
        # L5：软续写信号加分（连字符换行、虚词结尾等）
        if semantic_bonus:
            score = min(1.0, score + semantic_bonus)
        
        return TextFlowEdge(
            source=e1.handle,
            target=e2.handle,
            x_gap=x_gap,
            y_diff=y_diff,
            rotation_diff=rotation_diff,
            score=score,
        )
    
    # L5 语言层：语义关系分类
    #   "hard_break"      硬断，一定不合
    #   "soft_continue"   明显续写信号（连字符 / 虚词结尾），倾向合并
    #   "neutral"         没有强信号，交给几何评分
    SEMANTIC_HARD_BREAK = "hard_break"
    SEMANTIC_SOFT_CONTINUE = "soft_continue"
    SEMANTIC_NEUTRAL = "neutral"

    # 句尾"硬"结束标点。含常见中英文标点。
    _SENTENCE_END_CHARS = "。.!?！？;；"
    # 中文虚词结尾——大概率是续写而不是句子完结
    _CJK_CONTINUATION_TAILS = (
        "的", "和", "与", "及", "若", "为", "在", "以", "对", "由",
        "或", "而", "则", "但", "但是", "并", "且", "把", "被",
    )
    _TITLE_PATTERN = re.compile(
        r'^('
        r'\d+[.、)\s]|'                              # 1. 2、3)
        r'[一二三四五六七八九十]+[、.\s]|'          # 一、二.
        r'第[一二三四五六七八九十\d]+[章节条款项]?|'   # 第一章、第2节
        r'[①②③④⑤⑥⑦⑧⑨⑩]|'                       # ①②③
        r'[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]|'                        # 罗马数字
        r'[A-Z][.、)\s]'                             # A. B、
        r')',
        re.IGNORECASE,
    )

    def _classify_semantic_relation(self, prev_text: str, curr_text: str) -> str:
        """返回三态之一：hard_break / soft_continue / neutral"""
        prev_text = (prev_text or "").strip()
        curr_text = (curr_text or "").strip()
        if not prev_text or not curr_text:
            return self.SEMANTIC_NEUTRAL

        # ---- 硬断规则 ----
        # 后文以标题/序号开头
        if self._TITLE_PATTERN.match(curr_text):
            logger.debug(
                "L5 硬断(后文序号) %r → %r", prev_text[:20], curr_text[:20],
            )
            return self.SEMANTIC_HARD_BREAK
        # 前文本身就是简短独立标题（"一、设计依据"这种）
        # 阈值收紧到 12：长条目（如 "1、灰显淡显管线及..." 一整段话开头）不算标题
        if self._TITLE_PATTERN.match(prev_text) and len(prev_text) <= 12:
            logger.debug(
                "L5 硬断(前文是短标题) %r → %r", prev_text[:20], curr_text[:20],
            )
            return self.SEMANTIC_HARD_BREAK
        # 前文以句尾标点结尾 → 硬断
        # CAD 场景里"上一句以句号结尾"几乎 100% 说明前文完结，即便后文以
        # 顿号（"、具体维修改造..."）、数字（"1、"）或半角括号开头也算新句。
        # 例外：后文以闭合标点开头（如 "），..."），那是被拆碎的续写，不硬断。
        last_char = prev_text[-1]
        first_char = curr_text[0]
        if last_char in self._SENTENCE_END_CHARS:
            if first_char not in "）)]】》〕〗":
                logger.debug(
                    "L5 硬断(句尾标点后新句) %r → %r",
                    prev_text[:20], curr_text[:20],
                )
                return self.SEMANTIC_HARD_BREAK

        # 冒号 / 全角冒号引出编号列表：前文以 "：/:" 结尾 + 后文以 "、/,/1./①..." 开头
        # 这是 CAD 图纸"附注：、灰显..." 这类标题-列表结构，必须硬断。
        if last_char in "：:":
            if first_char in "、," or self._TITLE_PATTERN.match(curr_text):
                logger.debug(
                    "L5 硬断(冒号引出列表) %r → %r",
                    prev_text[:20], curr_text[:20],
                )
                return self.SEMANTIC_HARD_BREAK

        # 前文以"、/,"结尾 + 后文也以"、/,"开头 → 两条并列列表项之间
        if last_char in "。！？.!?" or (last_char == "、" and first_char == "、"):
            # 已在上面处理句尾标点；顿号-顿号是列表项分隔
            if last_char == "、" and first_char == "、":
                logger.debug(
                    "L5 硬断(并列列表项) %r → %r",
                    prev_text[:20], curr_text[:20],
                )
                return self.SEMANTIC_HARD_BREAK

        # ---- 软续写规则（不断且加分）----
        # 英文连字符换行：word- + 小写起头
        if last_char == "-" and first_char.isascii() and first_char.islower():
            return self.SEMANTIC_SOFT_CONTINUE
        # 前文以中文虚词结尾
        for tail in self._CJK_CONTINUATION_TAILS:
            if prev_text.endswith(tail):
                return self.SEMANTIC_SOFT_CONTINUE
        # 前文以逗号、顿号、分句符结尾 → 明显未结束
        if last_char in "，,、":
            return self.SEMANTIC_SOFT_CONTINUE
        # 后文以闭合括号/量词/单位结尾类符号起头 → 明显续写
        if first_char in "）)]】》〕〗":
            return self.SEMANTIC_SOFT_CONTINUE

        return self.SEMANTIC_NEUTRAL

    # 保留旧 API 名称的兼容包装（find_text_paths 之外的调用不多，但确保外部不炸）
    def _has_semantic_break(self, prev_text: str, curr_text: str) -> bool:
        return self._classify_semantic_relation(prev_text, curr_text) == self.SEMANTIC_HARD_BREAK
    
    def find_text_paths(self) -> List[List[str]]:
        """把实体聚成句子。

        L1 贪心合并（默认）：按 edge.score 降序，用并查集 Kruskal 风格合并；
        每次合并前做簇级验证，避免"两条弱边把三个实体桥成一句"。
        关闭 L1 时退化到旧 BFS 连通分量算法（保留回退）。
        """
        if not self.enable_greedy_merge:
            return self._paths_by_bfs()
        return self._paths_by_greedy()

    # ------------------------------------------------------------------
    # 旧 BFS 实现（保留作为回退）
    # ------------------------------------------------------------------
    def _paths_by_bfs(self) -> List[List[str]]:
        visited: Set[str] = set()
        paths: List[List[str]] = []
        # 预索引：handle → 相邻边的最小分数（用于估算合并信心）
        edge_score_by_pair: Dict[tuple[str, str], float] = {}
        for e in self.edges:
            key = tuple(sorted((e.source, e.target)))
            edge_score_by_pair[key] = e.score

        for handle in self.nodes:
            if handle in visited:
                continue
            path: List[str] = []
            queue = [handle]
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)
                path.append(current)
                for neighbor in self.adjacency.get(current, []):
                    if neighbor not in visited:
                        queue.append(neighbor)
            if path:
                path.sort(key=lambda h: self.nodes[h].x)
                paths.append(path)
                if len(path) == 1:
                    self.path_confidence[path[0]] = 1.0
                else:
                    scores = []
                    for i in range(len(path) - 1):
                        key = tuple(sorted((path[i], path[i + 1])))
                        if key in edge_score_by_pair:
                            scores.append(edge_score_by_pair[key])
                    self.path_confidence[path[0]] = min(scores) if scores else 0.0
        return paths

    # ------------------------------------------------------------------
    # L1：按分数贪心合并 + 簇级验证
    # ------------------------------------------------------------------
    def _paths_by_greedy(self) -> List[List[str]]:
        # 并查集
        parent: Dict[str, str] = {h: h for h in self.nodes}
        cluster_members: Dict[str, List[str]] = {h: [h] for h in self.nodes}
        cluster_bounds: Dict[str, tuple[float, float, float, float]] = {
            h: (e.x, e.y, e.right_edge, e.y + e.height)
            for h, e in self.nodes.items()
        }
        # 记录每个簇里"最强边"和"最弱边"分数，用于相对性验证
        cluster_max_score: Dict[str, float] = {h: 0.0 for h in self.nodes}
        cluster_min_score: Dict[str, float] = {h: 1.0 for h in self.nodes}

        def find(h: str) -> str:
            r = h
            while parent[r] != r:
                r = parent[r]
            while parent[h] != r:
                parent[h], h = r, parent[h]
            return r

        def union(a: str, b: str, edge_score: float) -> None:
            ra, rb = find(a), find(b)
            if ra == rb:
                return
            if len(cluster_members[ra]) < len(cluster_members[rb]):
                ra, rb = rb, ra
            parent[rb] = ra
            cluster_members[ra].extend(cluster_members[rb])
            cluster_members.pop(rb, None)
            a_box = cluster_bounds[ra]
            b_box = cluster_bounds.pop(rb)
            cluster_bounds[ra] = (
                min(a_box[0], b_box[0]),
                min(a_box[1], b_box[1]),
                max(a_box[2], b_box[2]),
                max(a_box[3], b_box[3]),
            )
            cluster_max_score[ra] = max(
                cluster_max_score[ra], cluster_max_score.pop(rb, 0.0), edge_score,
            )
            cluster_min_score[ra] = min(
                cluster_min_score[ra], cluster_min_score.pop(rb, 1.0), edge_score,
            )

        # 只保留超过绝对下限的边，按分数降序
        strong_edges = [e for e in self.edges if e.score >= self.min_edge_score]
        weak_dropped = len(self.edges) - len(strong_edges)
        if weak_dropped:
            self.reject_reasons["weak_edge"] = self.reject_reasons.get("weak_edge", 0) + weak_dropped
        strong_edges.sort(key=lambda e: e.score, reverse=True)

        bridge_barrier_rejects = 0
        bridge_weak_rejects = 0
        bridge_semantic_rejects = 0

        for edge in strong_edges:
            src, tgt = edge.source, edge.target
            ra, rb = find(src), find(tgt)
            if ra == rb:
                continue

            # 硬性：簇间合并 bbox 之间是否有 barrier
            if self.barriers is not None and self._bridge_crosses_barrier(
                cluster_bounds[ra], cluster_bounds[rb], self.nodes[src].scope,
            ):
                bridge_barrier_rejects += 1
                continue

            # 硬性：两簇中任意"边界对"存在语义 hard_break → 拒
            # 这样 A-B 允许合、B-C 允许合但 A-C 硬断的情况就不会因为 B 桥接被误合
            if self.enable_semantic_break and self._bridge_has_semantic_hard_break(
                cluster_members[ra], cluster_members[rb],
            ):
                bridge_semantic_rejects += 1
                continue

            # 相对性：若目标簇内部已经很紧（max_score 高），加进来的桥边分数不能
            # 显著低于簇内水平，否则就是典型的"桥接式误合"。阈值取 50%。
            # 只在簇有内部边时才做，避免对单实体簇误伤。
            rel_ok = True
            for root in (ra, rb):
                if len(cluster_members[root]) >= 2:
                    peak = cluster_max_score[root]
                    if peak > 0 and edge.score < 0.5 * peak:
                        rel_ok = False
                        break
            if not rel_ok:
                bridge_weak_rejects += 1
                logger.debug(
                    "L1 拒绝(桥接弱边) %s(peak_a=%.2f) - %s(peak_b=%.2f) edge=%.2f",
                    src, cluster_max_score[ra], tgt, cluster_max_score[rb], edge.score,
                )
                continue

            union(src, tgt, edge.score)

        if bridge_barrier_rejects:
            self.reject_reasons["bridge_barrier"] = (
                self.reject_reasons.get("bridge_barrier", 0) + bridge_barrier_rejects
            )
        if bridge_weak_rejects:
            self.reject_reasons["bridge_weak"] = (
                self.reject_reasons.get("bridge_weak", 0) + bridge_weak_rejects
            )
        if bridge_semantic_rejects:
            self.reject_reasons["bridge_semantic"] = (
                self.reject_reasons.get("bridge_semantic", 0) + bridge_semantic_rejects
            )

        paths: List[List[str]] = []
        for root, members in cluster_members.items():
            avg_h = sum(self.nodes[h].height for h in members) / max(len(members), 1)
            y_tol = max(avg_h * 0.8, 1e-6)
            members.sort(key=lambda h: (
                -round(self.nodes[h].y / y_tol) * y_tol,
                self.nodes[h].x,
            ))
            # 合并信心 = 簇内最弱一条边的分数；单实体簇为 1.0
            conf = 1.0 if len(members) == 1 else max(0.0, cluster_min_score.get(root, 1.0))
            self.path_confidence[members[0]] = conf
            paths.append(members)
        return paths

    def _bridge_has_semantic_hard_break(
        self,
        members_a: List[str],
        members_b: List[str],
    ) -> bool:
        """任意跨簇对存在 hard_break → 视为整簇不能合并。

        对簇 A 和 B 的每对 (a in A, b in B) 都问一次语义分类；判空快、
        字符串短，即便 O(m*n) 也不会真的成为热点。为了保险，把簇大小
        乘积限制在 400 以内（>= 20*20），大簇直接跳过（大簇通常本来就是错合，
        L1 相对性 / barrier 已经挡住了）。
        """
        if not members_a or not members_b:
            return False
        if len(members_a) * len(members_b) > 400:
            return False
        for ha in members_a:
            ea = self.nodes[ha]
            for hb in members_b:
                eb = self.nodes[hb]
                # 决定阅读顺序：先按 y（大在前），再按 x
                if (ea.y, -ea.x) > (eb.y, -eb.x):
                    prev, curr = ea, eb
                else:
                    prev, curr = eb, ea
                if self._classify_semantic_relation(prev.text, curr.text) == self.SEMANTIC_HARD_BREAK:
                    logger.debug(
                        "L1 拒绝(桥接跨簇语义硬断) %s(%r) - %s(%r)",
                        ha, ea.text[:20], hb, eb.text[:20],
                    )
                    return True
        return False

    def _bridge_crosses_barrier(
        self,
        box_a: tuple[float, float, float, float],
        box_b: tuple[float, float, float, float],
        scope: str,
    ) -> bool:
        """两个簇的合并包围盒之间是否穿过 barrier 线。

        通过判断：
        - 若 box_a 完全在 box_b 上方/下方 → 水平线阻挡检测
        - 若 box_a 完全在 box_b 左方/右方 → 垂直线阻挡检测
        - 上下 + 左右都重叠时视为"已经在一起"，不做额外检查
        """
        if self.barriers is None:
            return False
        a_left, a_bot, a_right, a_top = box_a
        b_left, b_bot, b_right, b_top = box_b
        x_min = min(a_left, b_left)
        x_max = max(a_right, b_right)
        y_min = min(a_bot, b_bot)
        y_max = max(a_top, b_top)

        # 上下分离 → 查横线是否穿过合并 x 范围
        if a_bot >= b_top or b_bot >= a_top:
            y_mid_1 = (a_top + a_bot) / 2.0
            y_mid_2 = (b_top + b_bot) / 2.0
            if self.barriers.horizontal_between(
                scope, y_mid_1, y_mid_2, x_min, x_max,
            ) is not None:
                return True
        # 左右分离 → 查竖线是否穿过合并 y 范围
        if a_right <= b_left or b_right <= a_left:
            x_mid_1 = (a_left + a_right) / 2.0
            x_mid_2 = (b_left + b_right) / 2.0
            if self.barriers.vertical_between(
                scope, x_mid_1, x_mid_2, y_min, y_max,
            ) is not None:
                return True
        return False


class TextReconstructor:
    """文本重建器：将碎片化的 CAD 文本实体重建为语义完整的句子"""
    
    def __init__(
        self,
        y_threshold_factor: float = 0.8,
        x_gap_threshold_factor: float = 3.0,
        rotation_threshold: float = 5.0,
        enable_semantic_break: bool = True,  # 方案2：语义分割判断
        enable_logical_grouping: bool = True,  # L4：按 style/height/tag/INSERT 分组
        height_ratio_tolerance: float = 0.30,
        next_line_gap_factor: float = 3.0,     # 有 L2 网格线兜底后可放宽段落续行 y 阈值
        barrier_index: Optional[BarrierIndex] = None,  # L2 网格线阻挡索引
        enable_greedy_merge: bool = True,      # L1：按打分贪心合并
        min_edge_score: float = 0.15,          # 弱边分数下限
        iou_split_threshold: float = 0.5,      # L3：bbox IoU 超过此值直接拒
    ):
        self.y_threshold_factor = y_threshold_factor
        self.x_gap_threshold_factor = x_gap_threshold_factor
        self.rotation_threshold = rotation_threshold
        self.enable_semantic_break = enable_semantic_break
        self.enable_logical_grouping = enable_logical_grouping
        self.height_ratio_tolerance = height_ratio_tolerance
        self.next_line_gap_factor = next_line_gap_factor
        self.barrier_index = barrier_index
        self.enable_greedy_merge = enable_greedy_merge
        self.min_edge_score = min_edge_score
        self.iou_split_threshold = iou_split_threshold
        self.sentence_counter = 0

    def reconstruct(
        self,
        entities: List[TextEntity],
    ) -> List[Sentence]:
        """重建句子

        Args:
            entities: 文本实体列表

        Returns:
            重建后的句子列表
        """
        # 汇总所有分组内的拒绝原因，供上层诊断
        self.last_reject_stats: Dict[str, int] = {}
        if not entities:
            return []

        # 按 scope 和 layer 分组
        groups = self._group_by_context(entities)

        sentences: List[Sentence] = []

        for (scope, layer), group_entities in groups.items():
            group_sentences = self._reconstruct_group(group_entities, scope)
            sentences.extend(group_sentences)

        return sentences
    
    def _group_by_context(
        self, entities: List[TextEntity]
    ) -> Dict[Tuple[str, str], List[TextEntity]]:
        """按 scope 和 layer 分组"""
        groups: Dict[Tuple[str, str], List[TextEntity]] = {}
        
        for entity in entities:
            key = (entity.scope, entity.layer)
            if key not in groups:
                groups[key] = []
            groups[key].append(entity)
        
        return groups
    
    def _reconstruct_group(
        self,
        entities: List[TextEntity],
        scope: str,
    ) -> List[Sentence]:
        """重建单个分组内的句子"""
        if not entities:
            return []
        
        # 构建文本流图
        graph = TextFlowGraph(
            y_threshold_factor=self.y_threshold_factor,
            x_gap_threshold_factor=self.x_gap_threshold_factor,
            rotation_threshold=self.rotation_threshold,
            enable_semantic_break=self.enable_semantic_break,
            enable_logical_grouping=self.enable_logical_grouping,
            height_ratio_tolerance=self.height_ratio_tolerance,
            next_line_gap_factor=self.next_line_gap_factor,
            barrier_index=self.barrier_index,
            enable_greedy_merge=self.enable_greedy_merge,
            min_edge_score=self.min_edge_score,
            iou_split_threshold=self.iou_split_threshold,
        )
        
        for entity in entities:
            graph.add_entity(entity)

        graph.build_edges()

        # 汇总拒绝原因到 reconstructor 层
        for reason, cnt in graph.reject_reasons.items():
            self.last_reject_stats[reason] = self.last_reject_stats.get(reason, 0) + cnt

        # 找出文本路径
        paths = graph.find_text_paths()

        # 将每条路径转换为句子
        sentences: List[Sentence] = []

        for path in paths:
            confidence = graph.path_confidence.get(path[0], 1.0) if path else 1.0
            sentence = self._path_to_sentence(path, graph.nodes, confidence)
            if sentence:
                sentences.append(sentence)

        return sentences

    def _path_to_sentence(
        self,
        path: List[str],
        nodes: Dict[str, TextEntity],
        merge_confidence: float = 1.0,
    ) -> Optional[Sentence]:
        """将路径转换为句子"""
        if not path:
            return None
        
        entities = [nodes[h] for h in path]
        
        # 按阅读顺序排序：先按 Y 降序（上面的行先），同行内按 X 升序
        # 使用字高作为"同行"的判断依据
        avg_height = sum(e.height for e in entities) / len(entities)
        y_tolerance = avg_height * 0.8
        
        def sort_key(e: TextEntity) -> Tuple[float, float]:
            # 将 Y 坐标量化到行，避免微小差异影响排序
            # Y 越大越靠上，所以用负数让靠上的排在前面
            quantized_y = round(e.y / y_tolerance) * y_tolerance
            return (-quantized_y, e.x)
        
        entities.sort(key=sort_key)
        
        # 合并文本
        merged_text = self._merge_texts(entities)
        
        if not merged_text.strip():
            return None
        
        # 选择主实体（优先级：MTEXT > ATTRIB > TEXT，然后取第一个）
        primary = self._select_primary_entity(entities)
        
        self.sentence_counter += 1
        
        return Sentence(
            sentence_id=f"sentence_{self.sentence_counter:04d}",
            text=merged_text,
            entities=entities,
            primary_entity=primary,
            merge_confidence=merge_confidence,
        )
    
    def _merge_texts(self, entities: List[TextEntity]) -> str:
        """合并文本，根据间隔决定是否加空格"""
        if not entities:
            return ""
        
        parts: List[str] = []
        
        for i, entity in enumerate(entities):
            text = entity.text.strip()
            if not text:
                continue
            
            if i == 0:
                parts.append(text)
                continue
            
            prev = entities[i - 1]
            prev_text = prev.text.strip()
            
            need_space = self._need_space_between(prev_text, text, prev, entity)
            
            if need_space and parts:
                parts.append(" ")
            parts.append(text)
        
        return "".join(parts)
    
    def _need_space_between(
        self,
        prev_text: str,
        curr_text: str,
        prev_entity: TextEntity,
        curr_entity: TextEntity,
    ) -> bool:
        """判断两个文本之间是否需要空格"""
        if not prev_text or not curr_text:
            return False
        
        # 前一个文本以标点结尾，不加空格
        if prev_text[-1] in "，。、；：！？,.:;!?-/":
            return False
        
        # 当前文本以标点开头，不加空格
        if curr_text[0] in "，。、；：！？,.:;!?-/":
            return False
        
        # 数字/单位模式紧密连接
        if re.match(r'^[DN]?\d|^\(\d', curr_text):
            return False
        
        # 前文以数字结尾，当前以单位开头
        if prev_text[-1].isdigit() and re.match(r'^(mm|cm|m|in|inch|ft|°|%|kg|g|lb)', curr_text, re.I):
            return False
        
        # 中文之间不加空格
        prev_is_cjk = self._is_cjk(prev_text[-1])
        curr_is_cjk = self._is_cjk(curr_text[0])
        if prev_is_cjk and curr_is_cjk:
            return False
        
        # 计算实际 X 间隔
        avg_height = (prev_entity.height + curr_entity.height) / 2
        x_gap = curr_entity.x - prev_entity.right_edge
        
        # 间隔很小（< 0.5 × 字高）不加空格
        if x_gap < avg_height * 0.5:
            return False
        
        # 间隔较大（> 1.5 × 字高）加空格
        if x_gap > avg_height * 1.5:
            return True
        
        # 默认：中英文混合时加空格，否则不加
        if prev_is_cjk != curr_is_cjk:
            return True
        
        return False
    
    @staticmethod
    def _is_cjk(char: str) -> bool:
        """判断字符是否是 CJK 字符"""
        return (
            "\u4e00" <= char <= "\u9fff" or
            "\u3040" <= char <= "\u30ff" or
            "\uac00" <= char <= "\ud7af"
        )
    
    def _select_primary_entity(self, entities: List[TextEntity]) -> TextEntity:
        """选择主实体"""
        if not entities:
            raise ValueError("entities cannot be empty")
        
        # 按优先级和位置排序
        # 优先级高的在前，同优先级按 X 坐标（最左边的在前）
        sorted_entities = sorted(
            entities,
            key=lambda e: (e.priority, e.x)
        )
        
        return sorted_entities[0]


def visual_length(text: str) -> float:
    """估算视觉宽度：CJK 全角按 2 计，其他按 1 计（保留旧行为，仅供外部展示用）"""
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


def estimate_text_width(text: str, height: float, width_factor: float = 0.6) -> float:
    """估算文本在 CAD 中的宽度。

    分别处理 CJK 与 ASCII：
    - CJK 字符宽度 ≈ 1.0 × 字高（方块字）
    - ASCII 字符宽度 ≈ width_factor × 字高（默认 0.6）

    旧实现"CJK 全角按 2 计 × 0.6h = 每字 1.2h"会显著高估中文文本宽度，
    导致后一段短文本的起点落在前段的"虚构 bbox"内部，被 x_overlap_too_deep 误拒。
    """
    if not text:
        return 0.0
    ascii_count = 0
    cjk_count = 0
    for ch in text:
        if (
            "\u4e00" <= ch <= "\u9fff"
            or "\u3040" <= ch <= "\u30ff"
            or "\uac00" <= ch <= "\ud7af"
            or "\uff00" <= ch <= "\uffef"
        ):
            cjk_count += 1
        else:
            ascii_count += 1
    return (cjk_count * 1.0 + ascii_count * width_factor) * height
