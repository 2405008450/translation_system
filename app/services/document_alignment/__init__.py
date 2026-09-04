"""双文档确定性对齐服务。"""

from .dp import AlignPair, align_block
from .parser import AlignUnit, parse_side

__all__ = ["AlignPair", "AlignUnit", "align_block", "parse_side"]
