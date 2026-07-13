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


class EntityPriority(Enum):
    """实体优先级：MTEXT > ATTRIB > TEXT"""
    MTEXT = 1      # 内部本来就是段落
    ATTRIB = 2     # 块内语义
    TEXT = 3       # 碎片


@dataclass
class TextEntity:
    """文本实体的完整信息"""
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
    ):
        self.y_threshold_factor = y_threshold_factor
        self.x_gap_threshold_factor = x_gap_threshold_factor
        self.rotation_threshold = rotation_threshold
        self.enable_semantic_break = enable_semantic_break
        
        self.nodes: Dict[str, TextEntity] = {}
        self.edges: List[TextFlowEdge] = []
        self.adjacency: Dict[str, List[str]] = {}
    
    def add_entity(self, entity: TextEntity) -> None:
        """添加实体节点"""
        self.nodes[entity.handle] = entity
        if entity.handle not in self.adjacency:
            self.adjacency[entity.handle] = []
    
    def build_edges(self) -> None:
        """构建边：计算所有实体对之间的空间连续性"""
        handles = list(self.nodes.keys())
        
        for i, h1 in enumerate(handles):
            for h2 in handles[i+1:]:
                e1 = self.nodes[h1]
                e2 = self.nodes[h2]
                
                edge = self._compute_edge(e1, e2)
                if edge and edge.score > 0:
                    self.edges.append(edge)
                    self.adjacency[h1].append(h2)
                    self.adjacency[h2].append(h1)
    
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
            return None
        
        # 不同 scope 不合并
        if e1.scope != e2.scope:
            return None
        
        avg_height = (e1.height + e2.height) / 2
        
        # 旋转角度差异检查
        rotation_diff = abs(e1.rotation - e2.rotation)
        if rotation_diff > 180:
            rotation_diff = 360 - rotation_diff
        if rotation_diff > self.rotation_threshold:
            return None
        
        y_diff = abs(e1.y - e2.y)
        
        # 模式1：同行合并（Y 接近）
        y_threshold_same_line = avg_height * self.y_threshold_factor
        is_same_line = y_diff <= y_threshold_same_line
        
        # 模式2：换行合并（Y 差 1-2 倍字高，但 X 范围有重叠或接近）
        # 典型的多行文本，行间距是 1.2-1.5 倍字高
        y_threshold_next_line = avg_height * 2.0
        is_next_line = y_threshold_same_line < y_diff <= y_threshold_next_line
        
        if is_next_line:
            # 换行合并需要额外条件：X 范围有重叠，说明是同一段落
            # 上一行和下一行的 X 范围应该有交集
            e1_left, e1_right = e1.x, e1.right_edge
            e2_left, e2_right = e2.x, e2.right_edge
            
            # 检查 X 范围是否有重叠
            x_overlap = min(e1_right, e2_right) - max(e1_left, e2_left)
            
            # 或者下一行的起点接近上一行的起点（左对齐的段落）
            x_start_diff = abs(e1.x - e2.x)
            x_start_close = x_start_diff < avg_height * 3
            
            if x_overlap <= 0 and not x_start_close:
                # X 范围没有重叠，也不是左对齐，不合并
                return None
        elif not is_same_line:
            # 既不是同行也不是换行，不合并
            return None
        
        # 方案2：语义分割判断
        # 如果前一个文本以句号结尾，且后一个以大写/中文开头，说明是两句话，不合并
        if self.enable_semantic_break:
            # 确定阅读顺序（左边的在前）
            left_entity = e1 if e1.x <= e2.x else e2
            right_entity = e2 if e1.x <= e2.x else e1
            
            if self._has_semantic_break(left_entity.text, right_entity.text):
                logger.debug(
                    "语义分割：'%s' 和 '%s' 之间有语义边界，不合并",
                    left_entity.text[:20], right_entity.text[:20]
                )
                return None
        
        # X 间隔计算（确保 e1 在 e2 左边）
        if e1.x > e2.x:
            e1, e2 = e2, e1
        
        x_gap = e2.x - e1.right_edge
        x_threshold = avg_height * self.x_gap_threshold_factor
        
        # 对于同行合并，检查 X 间隔
        if is_same_line:
            # 允许轻微重叠（负间隔），但不能重叠太多
            if x_gap < -avg_height:
                return None
            if x_gap > x_threshold:
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
        
        return TextFlowEdge(
            source=e1.handle,
            target=e2.handle,
            x_gap=x_gap,
            y_diff=y_diff,
            rotation_diff=rotation_diff,
            score=score,
        )
    
    def _has_semantic_break(self, prev_text: str, curr_text: str) -> bool:
        """判断两个文本之间是否有语义边界（方案2）
        
        规则：
        1. 前文以句号结尾（。.!?！？）+ 后文以大写/中文/序号开头
        2. 或者：前文是独立的标题/序号（如 "一、设计依据"）
        3. 或者：后文以标题序号开头（如 "1." "一、" "第一"）
        """
        if not prev_text or not curr_text:
            return False
        
        prev_text = prev_text.strip()
        curr_text = curr_text.strip()
        
        if not prev_text or not curr_text:
            return False
        
        # 句子结束标点
        sentence_endings = "。.!?！？"
        
        # 标题/序号开头模式
        title_pattern = re.compile(
            r'^('
            r'\d+[.、)\s]|'                          # 数字序号：1. 2、3) 
            r'[一二三四五六七八九十]+[、.\s]|'         # 中文数字：一、二.
            r'第[一二三四五六七八九十\d]+[章节条款项]?|'  # 第一章、第2节
            r'[①②③④⑤⑥⑦⑧⑨⑩]|'                     # 圆圈数字
            r'[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]|'                     # 罗马数字
            r'[A-Z][.、)\s]'                          # 字母序号：A. B、
            r')',
            re.IGNORECASE
        )
        
        # 检查后文是否以标题/序号开头
        curr_is_title_start = title_pattern.match(curr_text) is not None
        
        # 检查前文是否以标题/序号开头（独立标题）
        prev_is_title = title_pattern.match(prev_text) is not None
        
        # 检查前文是否以句号结尾
        prev_ends_with_period = prev_text[-1] in sentence_endings
        
        # 规则1：如果后文以标题/序号开头，判断为语义边界
        if curr_is_title_start:
            logger.debug(
                "语义分割（规则1-序号开头）：'%s' → '%s'",
                prev_text[:20], curr_text[:20]
            )
            return True
        
        # 规则2：如果前文是标题/序号，判断为语义边界
        if prev_is_title:
            logger.debug(
                "语义分割（规则2-前文是标题）：'%s' → '%s'",
                prev_text[:20], curr_text[:20]
            )
            return True
        
        # 规则3：前文以句号结尾 + 后文以大写/中文开头
        if prev_ends_with_period:
            first_char = curr_text[0]
            
            # 中文开头（可能是新句子）
            is_cjk_start = '\u4e00' <= first_char <= '\u9fff'
            
            # 英文大写开头
            is_uppercase_start = first_char.isupper()
            
            if is_cjk_start or is_uppercase_start:
                logger.debug(
                    "语义分割（规则3-句号后新句子）：'%s' → '%s'",
                    prev_text[:20], curr_text[:20]
                )
                return True
        
        return False
    
    def find_text_paths(self) -> List[List[str]]:
        """找出所有文本路径（连通分量）"""
        visited: Set[str] = set()
        paths: List[List[str]] = []
        
        for handle in self.nodes:
            if handle in visited:
                continue
            
            # BFS 找连通分量
            path = []
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
                # 按 X 坐标排序，确保阅读顺序
                path.sort(key=lambda h: self.nodes[h].x)
                paths.append(path)
        
        return paths


class TextReconstructor:
    """文本重建器：将碎片化的 CAD 文本实体重建为语义完整的句子"""
    
    def __init__(
        self,
        y_threshold_factor: float = 0.8,
        x_gap_threshold_factor: float = 3.0,
        rotation_threshold: float = 5.0,
        enable_semantic_break: bool = True,  # 方案2：语义分割判断
    ):
        self.y_threshold_factor = y_threshold_factor
        self.x_gap_threshold_factor = x_gap_threshold_factor
        self.rotation_threshold = rotation_threshold
        self.enable_semantic_break = enable_semantic_break
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
        )
        
        for entity in entities:
            graph.add_entity(entity)
        
        graph.build_edges()
        
        # 找出文本路径
        paths = graph.find_text_paths()
        
        # 将每条路径转换为句子
        sentences: List[Sentence] = []
        
        for path in paths:
            sentence = self._path_to_sentence(path, graph.nodes)
            if sentence:
                sentences.append(sentence)
        
        return sentences
    
    def _path_to_sentence(
        self,
        path: List[str],
        nodes: Dict[str, TextEntity],
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
    """估算视觉宽度：CJK 全角按 2 计，其他按 1 计"""
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
    """估算文本在 CAD 中的宽度"""
    return visual_length(text) * height * width_factor
